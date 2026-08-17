"""Managed resume storage regression tests."""

import tempfile
from pathlib import Path
from unittest.mock import patch

from resume_store import (
    UnmanagedResumePathError,
    audit_managed_resumes,
    cleanup_unreferenced_managed_resumes,
    cleanup_orphan_managed_resumes,
    delete_managed_resume,
    repair_invalid_resume_references,
    resolve_managed_resume,
    store_resume_copy,
)


def test_store_resume_uses_random_managed_name_and_relative_reference():
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        source = root / "张三的简历.txt"
        source.write_text("Java 开发经验", encoding="utf-8")

        stored = store_resume_copy(source, base_dir=root)
        resolved = resolve_managed_resume(
            stored.reference,
            base_dir=root,
            require_exists=True,
        )

        assert stored.original_name == source.name
        assert stored.artifact_id in resolved.name
        assert "张三" not in resolved.name
        assert not Path(stored.reference).is_absolute()
        assert resolved.read_text(encoding="utf-8") == "Java 开发经验"


def test_resolve_accepts_legacy_absolute_path_inside_managed_directory():
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        legacy = root / "resumes" / "旧姓名_g1_20260730.pdf"
        legacy.parent.mkdir()
        legacy.write_bytes(b"legacy")

        resolved = resolve_managed_resume(
            str(legacy),
            base_dir=root,
            require_exists=True,
        )

        assert resolved == legacy.resolve()


def test_delete_rejects_external_resume_reference_without_touching_file():
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        external = root / "outside.pdf"
        external.write_bytes(b"private")

        try:
            delete_managed_resume(str(external), base_dir=root)
            assert False, "Expected UnmanagedResumePathError"
        except UnmanagedResumePathError:
            pass

        assert external.read_bytes() == b"private"


def test_delete_managed_resume_removes_only_the_managed_copy():
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        source = root / "source.txt"
        source.write_text("resume", encoding="utf-8")
        stored = store_resume_copy(source, base_dir=root)

        assert delete_managed_resume(stored.reference, base_dir=root)
        assert source.exists()
        assert not resolve_managed_resume(stored.reference, base_dir=root).exists()


def test_audit_reports_valid_missing_unmanaged_and_orphan_storage():
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        resumes_dir = root / "resumes"
        resumes_dir.mkdir()
        valid = resumes_dir / "valid.pdf"
        orphan = resumes_dir / "orphan.docx"
        external = root / "external.pdf"
        valid.write_bytes(b"valid")
        orphan.write_bytes(b"orphan-data")
        external.write_bytes(b"external")
        candidates = [
            {"resume_file": "resumes/valid.pdf"},
            {"resume_file": str(valid)},
            {"resume_file": "resumes/missing.pdf"},
            {"resume_file": str(external)},
            {"resume_file": ""},
            {},
        ]

        report = audit_managed_resumes(candidates, base_dir=root)

        assert report.reference_count == 4
        assert report.valid_reference_count == 2
        assert report.missing_reference_count == 1
        assert report.unmanaged_reference_count == 1
        assert report.managed_file_count == 2
        assert report.referenced_file_count == 1
        assert report.orphan_file_count == 1
        assert report.shared_file_count == 1
        assert report.managed_bytes == len(b"valid") + len(b"orphan-data")
        assert report.orphan_bytes == len(b"orphan-data")
        assert report.issue_count == 3
        assert valid.exists()
        assert orphan.exists()
        assert external.exists()


def test_audit_does_not_create_missing_managed_directory():
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)

        report = audit_managed_resumes([], base_dir=root)

        assert report.reference_count == 0
        assert report.managed_file_count == 0
        assert report.issue_count == 0
        assert not (root / "resumes").exists()


def test_audit_counts_non_string_resume_reference_as_unmanaged():
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)

        report = audit_managed_resumes(
            [{"resume_file": 123}, {"resume_file": None}],
            base_dir=root,
        )

        assert report.reference_count == 1
        assert report.unmanaged_reference_count == 1
        assert report.issue_count == 1


def test_cleanup_deletes_only_targets_without_active_references():
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        resumes_dir = root / "resumes"
        resumes_dir.mkdir()
        shared = resumes_dir / "shared.pdf"
        orphan = resumes_dir / "orphan.pdf"
        external = root / "external.pdf"
        shared.write_bytes(b"shared")
        orphan.write_bytes(b"orphan")
        external.write_bytes(b"external")

        result = cleanup_unreferenced_managed_resumes(
            [shared, orphan, external],
            [{"resume_file": "resumes/shared.pdf"}],
            base_dir=root,
        )

        assert result.target_file_count == 2
        assert result.deleted_file_count == 1
        assert result.retained_file_count == 1
        assert result.unmanaged_reference_count == 1
        assert result.reclaimed_bytes == len(b"orphan")
        assert shared.exists()
        assert not orphan.exists()
        assert external.exists()


def test_repair_invalid_references_clears_resume_state_and_restores_score():
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        valid = root / "resumes" / "valid.pdf"
        valid.parent.mkdir()
        valid.write_bytes(b"valid")
        external = root / "external.pdf"
        external.write_bytes(b"external")
        missing = {
            "resume_file": "resumes/missing.pdf",
            "resume_artifact_id": "missing",
            "resume_eval_adjustment": 15,
            "rule_score": 70,
            "llm_adjustment": 5,
            "match_score": 90,
            "score_breakdown": {"resume_adjustment": 15, "total": 90},
        }
        unmanaged = {
            "resume_file": str(external),
            "resume_eval_reason": "旧评估",
            "rule_score": 60,
            "match_score": 70,
        }
        stale = {
            "resume_file": None,
            "resume_eval_adjustment": 10,
            "rule_score": 65,
            "match_score": 75,
        }
        valid_candidate = {
            "resume_file": "resumes/valid.pdf",
            "resume_eval_adjustment": 5,
            "match_score": 70,
        }

        result = repair_invalid_resume_references(
            [missing, unmanaged, stale, valid_candidate],
            base_dir=root,
        )

        assert result.repaired_candidate_count == 3
        assert result.missing_reference_count == 1
        assert result.unmanaged_reference_count == 1
        assert result.stale_metadata_count == 1
        assert "resume_file" not in missing
        assert "resume_eval_adjustment" not in missing
        assert missing["match_score"] == 75
        assert missing["score_breakdown"] == {"total": 75}
        assert "resume_file" not in unmanaged
        assert unmanaged["match_score"] == 60
        assert "resume_eval_adjustment" not in stale
        assert valid_candidate["resume_file"] == "resumes/valid.pdf"
        assert external.exists()


def test_clear_candidate_resume_state_keeps_rejected_score_frozen():
    """已淘汰记录清除简历状态：分数与推荐等级冻结，不回算出非淘汰分。"""
    from resume_store import clear_candidate_resume_state

    candidate = {
        "resume_file": "resumes/g1.pdf",
        "resume_eval_adjustment": 10,
        "rule_score": 42,
        "llm_adjustment": 15,
        "match_score": 0,
        "recommend_level": "未通过",
        "qualification_status": "rejected",
        "score_breakdown": {"base": 25, "skill": 12, "resume_adjustment": 10, "total": 42},
    }

    clear_candidate_resume_state(candidate)

    assert "resume_file" not in candidate
    assert candidate["match_score"] == 0
    assert candidate["recommend_level"] == "未通过"
    assert candidate["rule_score"] == 42
    assert candidate["score_breakdown"]["total"] == 42
    assert "resume_adjustment" not in candidate["score_breakdown"]


def test_audit_reports_resume_metadata_without_a_file_reference():
    with tempfile.TemporaryDirectory() as tmpdir:
        report = audit_managed_resumes(
            [{"resume_eval_adjustment": 5}],
            base_dir=Path(tmpdir),
        )

        assert report.stale_metadata_count == 1
        assert report.issue_count == 1


def test_orphan_cleanup_reports_scan_failure_without_raising_after_commit():
    with tempfile.TemporaryDirectory() as tmpdir:
        with patch(
            "resume_store._managed_resume_files",
            side_effect=PermissionError("denied"),
        ):
            result = cleanup_orphan_managed_resumes(
                [],
                base_dir=Path(tmpdir),
            )

        assert result.deleted_file_count == 0
        assert result.scan_error_count == 1
        assert result.failure_count == 1
