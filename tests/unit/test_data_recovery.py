"""Transactional migration and local backup/restore guarantees."""
import json
import os
import tempfile
import zipfile
from pathlib import Path

from data_recovery import (
    AUTOMATIC_BACKUP_RETENTION,
    RuntimeDataPaths,
    _lock_owner_alive,
    create_backup_package,
    ensure_runtime_data_schema,
    inspect_backup,
    recover_pending_transaction,
    restore_backup,
)


def _write_legacy_runtime(root: Path) -> None:
    (root / "job_config.json").write_text(
        json.dumps(
            {
                "job_requirements": {
                    "Java 工程师": {
                        "min_exp": 3,
                        "edu": "本科",
                    }
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (root / "candidates_all.json").write_text(
        json.dumps(
            [{
                "geek_id": "g1",
                "name": "候选人甲",
                "job_name": "Java工程师",
                "match_score": 75,
                "resume_file": "resumes/resume-a.txt",
            }],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (root / "contact_queue.json").write_text(
        json.dumps(
            {
                "version": 1,
                "items": [{
                    "queue_id": "q1",
                    "geek_id": "g1",
                    "job_name": "Java工程师",
                    "status": "待发送",
                }],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    resumes = root / "resumes"
    resumes.mkdir()
    (resumes / "resume-a.txt").write_text(
        "private resume",
        encoding="utf-8",
    )


def test_schema_migration_commits_three_files_with_one_generation():
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        _write_legacy_runtime(root)

        result = ensure_runtime_data_schema(root)

        assert result["changed"] is True
        assert result["unresolved_candidate_count"] == 0
        assert result["unresolved_queue_count"] == 0
        config = json.loads(
            (root / "job_config.json").read_text(encoding="utf-8")
        )
        candidates = json.loads(
            (root / "candidates_all.json").read_text(encoding="utf-8")
        )
        queue = json.loads(
            (root / "contact_queue.json").read_text(encoding="utf-8")
        )
        job_uuid = config["job_requirements"]["Java 工程师"]["job_uuid"]
        assert config["schema_version"] == 2
        assert candidates[0]["schema_version"] == 2
        assert candidates[0]["job_uuid"] == job_uuid
        assert candidates[0]["job_name"] == "Java 工程师"
        assert queue["version"] == 2
        assert queue["items"][0]["job_uuid"] == job_uuid
        manifest = json.loads(
            (root / ".data_manifest.json").read_text(encoding="utf-8")
        )
        assert manifest["generation"] == result["transaction_id"]
        assert not (root / ".data_transaction.json").exists()


def test_interrupted_schema_migration_completes_on_next_start():
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        _write_legacy_runtime(root)
        replacements = []

        def fail_after_first(_stage, relative):
            replacements.append(relative)
            if len(replacements) == 1:
                raise OSError("simulated power loss")

        try:
            ensure_runtime_data_schema(
                root,
                failure_injector=fail_after_first,
            )
        except OSError as exc:
            assert "power loss" in str(exc)
        else:
            raise AssertionError("failure injection must interrupt migration")

        assert (root / ".data_transaction.json").is_file()
        recovery = recover_pending_transaction(root)

        assert recovery["recovered"] is True
        assert recovery["action"] == "complete"
        assert not (root / ".data_transaction.json").exists()
        assert (
            json.loads(
                (root / "contact_queue.json").read_text(encoding="utf-8")
            )["version"]
            == 2
        )


def test_manual_backup_and_restore_include_managed_resume():
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir) / "runtime"
        root.mkdir()
        _write_legacy_runtime(root)
        ensure_runtime_data_schema(root)
        package = Path(tmpdir) / "backup.zip"

        backup = create_backup_package(root, package)

        assert Path(backup["path"]).is_file()
        assert backup["candidate_count"] == 1
        assert backup["job_count"] == 1
        assert backup["queue_count"] == 1
        assert backup["resume_count"] == 1

        (root / "candidates_all.json").write_text(
            "[]",
            encoding="utf-8",
        )
        (root / "resumes" / "resume-a.txt").write_text(
            "changed",
            encoding="utf-8",
        )
        old_orphan = root / "resumes" / "old-orphan.txt"
        old_orphan.write_text("obsolete", encoding="utf-8")
        restored = restore_backup(root, package)

        assert restored["candidate_count"] == 1
        assert restored["resume_cleanup_count"] == 1
        assert restored["resume_cleanup_bytes"] == len(b"obsolete")
        assert restored["resume_cleanup_failed_count"] == 0
        assert not old_orphan.exists()
        assert (
            (root / "resumes" / "resume-a.txt").read_text(
                encoding="utf-8"
            )
            == "private resume"
        )


def test_backup_integrity_failure_blocks_restore_preview():
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir) / "runtime"
        root.mkdir()
        _write_legacy_runtime(root)
        ensure_runtime_data_schema(root)
        package = Path(tmpdir) / "backup.zip"
        create_backup_package(root, package)
        extracted = Path(tmpdir) / "extracted"
        with zipfile.ZipFile(package, "r") as archive:
            archive.extractall(extracted)
        (extracted / "candidates_all.json").write_text(
            "[]",
            encoding="utf-8",
        )

        try:
            inspect_backup(extracted)
        except ValueError as exc:
            assert "完整性校验失败" in str(exc)
        else:
            raise AssertionError("tampered backup must be rejected")


def test_automatic_recovery_points_rotate_to_bounded_count():
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        _write_legacy_runtime(root)
        ensure_runtime_data_schema(root)
        paths = RuntimeDataPaths.from_base_dir(root)
        automatic = paths.backups_dir / "automatic"
        original = sorted(path.name for path in automatic.iterdir())
        for index in range(AUTOMATIC_BACKUP_RETENTION + 2):
            (root / "contact_queue.json").write_text(
                json.dumps(
                    {"version": 1, "items": [], "nonce": index}
                ),
                encoding="utf-8",
            )
            ensure_runtime_data_schema(root)

        backups = [path for path in automatic.iterdir() if path.is_dir()]
        assert len(backups) <= AUTOMATIC_BACKUP_RETENTION
        assert original or backups


def test_transaction_cleanup_refuses_stage_directory_outside_workspace():
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir) / "runtime"
        root.mkdir()
        _write_legacy_runtime(root)
        paths = RuntimeDataPaths.from_base_dir(root)
        paths.transaction_dir.mkdir()
        paths.journal.write_text(
            json.dumps({
                "version": 1,
                "status": "committed",
                "transaction_id": "malicious",
                "stage_dir": str(root),
                "files": {},
            }),
            encoding="utf-8",
        )

        try:
            recover_pending_transaction(root)
        except ValueError as exc:
            assert "路径越界" in str(exc)
        else:
            raise AssertionError("out-of-scope cleanup must be rejected")

        assert (root / "job_config.json").is_file()
        assert (root / "resumes" / "resume-a.txt").is_file()


def test_zip_path_traversal_is_rejected_before_extraction():
    with tempfile.TemporaryDirectory() as tmpdir:
        package = Path(tmpdir) / "malicious.zip"
        with zipfile.ZipFile(package, "w") as archive:
            archive.writestr("../outside.txt", "unsafe")

        try:
            inspect_backup(package)
        except ValueError as exc:
            assert "不安全路径" in str(exc)
        else:
            raise AssertionError("zip traversal must be rejected")

        assert not (Path(tmpdir).parent / "outside.txt").exists()


def test_live_process_lock_is_never_treated_as_stale():
    with tempfile.TemporaryDirectory() as tmpdir:
        lock = Path(tmpdir) / ".data_transaction.lock"
        lock.write_text(f"{os.getpid()} 0", encoding="ascii")

        assert _lock_owner_alive(lock) is True

        lock.write_text("malformed", encoding="ascii")
        assert _lock_owner_alive(lock) is None
