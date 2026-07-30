"""Privacy and integrity guarantees for support diagnostic packages."""
import json
import tempfile
import zipfile
from pathlib import Path
from unittest.mock import patch

import diagnostic_package
from diagnostic_package import (
    DiagnosticPrivacyError,
    create_diagnostic_package,
    sanitize_diagnostic_text,
)


JOB_UUID = "d4954841-560c-4e8c-9993-b06b19ab1b38"


def _write_runtime(root: Path) -> None:
    (root / "job_config.json").write_text(
        json.dumps({
            "schema_version": 2,
            "job_requirements": {
                "绝密岗位": {
                    "job_uuid": JOB_UUID,
                    "min_exp": 3,
                }
            },
        }, ensure_ascii=False),
        encoding="utf-8",
    )
    (root / "candidates_all.json").write_text(
        json.dumps([{
            "schema_version": 2,
            "geek_id": "geek-secret-9988",
            "name": "张三",
            "job_uuid": JOB_UUID,
            "job_name": "绝密岗位",
            "phone": "13800138000",
            "email": "person@example.com",
            "resume_file": "resumes/张三.pdf",
        }], ensure_ascii=False),
        encoding="utf-8",
    )
    (root / "contact_queue.json").write_text(
        json.dumps({
            "version": 2,
            "items": [{
                "queue_id": "q1",
                "geek_id": "geek-secret-9988",
                "job_uuid": JOB_UUID,
                "job_name": "绝密岗位",
                "status": "待发送",
            }],
        }, ensure_ascii=False),
        encoding="utf-8",
    )
    (root / "api_config.local.json").write_text(
        json.dumps({
            "api_provider": "custom",
            "base_url": "https://private.example.com/v1",
            "model": "private-model",
            "api_key": "sk-super-secret-value",
        }),
        encoding="utf-8",
    )
    logs = root / "logs"
    logs.mkdir()
    (logs / "app-20260730.log").write_text(
        "[12:00:00] [简历评估] 正在评估 张三...\n"
        "phone=13800138000 email=person@example.com\n"
        "Authorization: Bearer abc.def.secret\n"
        "api_key=sk-super-secret-value\n"
        "path=C:\\Users\\private-user\\Downloads\\张三.pdf\n",
        encoding="utf-8",
    )
    (logs / "run-20260730.log").write_text(
        "处理岗位：绝密岗位，geekId=geek-secret-9988，HTTP 429\n",
        encoding="utf-8",
    )


def test_sanitizer_removes_common_credentials_and_personal_identifiers():
    text = (
        "张三 geek-secret 绝密岗位 13800138000 person@example.com "
        "11010519491231002X Authorization=Bearer abc.def "
        "api_key=sk-super-secret-value "
        "C:\\Users\\private-user\\Downloads\\resume.pdf HTTP 429"
    )
    sanitized = sanitize_diagnostic_text(text, {
        "张三": "<candidate-0001>",
        "geek-secret": "<candidate-0001>",
        "绝密岗位": "<job-001>",
        "sk-super-secret-value": "<redacted-secret>",
    })

    for raw in (
        "张三",
        "geek-secret",
        "绝密岗位",
        "13800138000",
        "person@example.com",
        "11010519491231002X",
        "private-user",
        "sk-super-secret-value",
        "abc.def",
    ):
        assert raw not in sanitized
    assert "HTTP 429" in sanitized
    assert "<candidate-0001>" in sanitized


def test_package_contains_only_allowlisted_summaries_and_redacted_logs():
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir) / "runtime"
        root.mkdir()
        _write_runtime(root)
        package = Path(tmpdir) / "diagnostic.zip"

        result = create_diagnostic_package(
            root,
            package,
            app_version="9.9.9",
            runtime_context={
                "screen_width": 3840,
                "screen_height": 2160,
                "browser_connected": True,
                "unsafe_extra": "must not appear",
            },
        )

        assert result["privacy_checked"] is True
        assert result["candidate_count"] == 1
        with zipfile.ZipFile(package, "r") as archive:
            names = set(archive.namelist())
            assert names == {
                "README.txt",
                "diagnostic-summary.json",
                "logs/app-20260730.log",
                "logs/run-20260730.log",
                "manifest.json",
            }
            combined = b"\n".join(
                archive.read(name)
                for name in names
            ).decode("utf-8")
            for raw in (
                "张三",
                "geek-secret-9988",
                "绝密岗位",
                "13800138000",
                "person@example.com",
                "private-user",
                "sk-super-secret-value",
                "abc.def.secret",
                "private.example.com",
                "unsafe_extra",
            ):
                assert raw not in combined
            summary = json.loads(archive.read(
                "diagnostic-summary.json"
            ))
            assert summary["data"]["candidates"]["candidate_count"] == 1
            assert summary["data"]["job_config"]["job_count"] == 1
            assert summary["ui"]["screen_width"] == 3840
            assert summary["privacy"]["raw_candidate_files_included"] is False


def test_privacy_audit_failure_preserves_existing_destination():
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir) / "runtime"
        root.mkdir()
        _write_runtime(root)
        package = Path(tmpdir) / "diagnostic.zip"
        package.write_bytes(b"previous-good-package")

        with patch.object(
            diagnostic_package,
            "_privacy_audit",
            side_effect=DiagnosticPrivacyError("simulated leak"),
        ):
            try:
                create_diagnostic_package(
                    root,
                    package,
                    app_version="9.9.9",
                )
            except DiagnosticPrivacyError:
                pass
            else:
                raise AssertionError("privacy failure must stop export")

        assert package.read_bytes() == b"previous-good-package"
