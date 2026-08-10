import tempfile
from pathlib import Path
from unittest.mock import patch

from resume_import_service import (
    ResumeCandidateNotFoundError,
    ResumeCopyError,
    ResumePersistenceError,
    persist_candidate_resume,
)
from storage import load_candidates_all, save_candidates_all


def test_persist_candidate_resume_replaces_reference_and_old_evaluation():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        candidates_path = root / "candidates.json"
        old_resume = root / "resumes" / "old.pdf"
        old_resume.parent.mkdir()
        old_resume.write_bytes(b"old")
        source = root / "new.txt"
        source.write_text("Java 开发经验 " * 10, encoding="utf-8")
        save_candidates_all(
            [
                {
                    "geek_id": "g1",
                    "job_name": "Java 工程师",
                    "rule_score": 70,
                    "llm_adjustment": 5,
                    "match_score": 90,
                    "resume_file": "resumes/old.pdf",
                    "resume_eval_adjustment": 15,
                    "resume_eval_reason": "旧评估",
                }
            ],
            candidates_path,
        )

        result = persist_candidate_resume(
            source,
            identity=("g1", "Java 工程师"),
            candidates_path=candidates_path,
            base_dir=root,
            imported_at="2026-08-10 10:00:00",
        )
        saved = load_candidates_all(candidates_path)[0]

        assert not old_resume.exists()
        assert saved["resume_file"] != "resumes/old.pdf"
        assert (root / saved["resume_file"]).is_file()
        assert saved["resume_imported_at"] == "2026-08-10 10:00:00"
        assert saved["match_score"] == 75
        assert "resume_eval_adjustment" not in saved
        assert "resume_eval_reason" not in saved
        assert result.candidate == saved
        assert result.cleanup.deleted_file_count == 1


def test_persist_candidate_resume_removes_new_copy_when_candidate_disappeared():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        candidates_path = root / "candidates.json"
        candidates_path.write_text("[]", encoding="utf-8")
        source = root / "new.txt"
        source.write_text("Java 开发经验 " * 10, encoding="utf-8")

        try:
            persist_candidate_resume(
                source,
                identity=("missing", "Java 工程师"),
                candidates_path=candidates_path,
                base_dir=root,
                imported_at="2026-08-10 10:00:00",
            )
        except ResumeCandidateNotFoundError:
            pass
        else:
            raise AssertionError("missing candidate should fail")

        managed_files = list((root / "resumes").glob("*"))
        assert managed_files == []


def test_persist_candidate_resume_reclaims_copy_after_confirmed_write_failure():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        candidates_path = root / "candidates.json"
        candidates_path.write_text("[]", encoding="utf-8")
        source = root / "new.txt"
        source.write_text("Java 开发经验 " * 10, encoding="utf-8")

        with patch(
            "resume_import_service.mutate_candidates_with_resume_cleanup",
            side_effect=OSError("write failed"),
        ):
            try:
                persist_candidate_resume(
                    source,
                    identity=("g1", "Java 工程师"),
                    candidates_path=candidates_path,
                    base_dir=root,
                    imported_at="2026-08-10 10:00:00",
                )
            except ResumePersistenceError as exc:
                assert exc.copy_retained is False
            else:
                raise AssertionError("persistence failure should be classified")

        assert list((root / "resumes").glob("*")) == []


def test_persist_candidate_resume_retains_copy_when_write_state_is_uncertain():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        source = root / "new.txt"
        source.write_text("Java 开发经验 " * 10, encoding="utf-8")

        with (
            patch(
                "resume_import_service.mutate_candidates_with_resume_cleanup",
                side_effect=OSError("write result unknown"),
            ),
            patch(
                "resume_import_service.read_candidates_snapshot",
                side_effect=OSError("cannot verify"),
            ),
        ):
            try:
                persist_candidate_resume(
                    source,
                    identity=("g1", "Java 工程师"),
                    candidates_path=root / "candidates.json",
                    base_dir=root,
                    imported_at="2026-08-10 10:00:00",
                )
            except ResumePersistenceError as exc:
                assert exc.copy_retained is True
            else:
                raise AssertionError("uncertain persistence should be classified")

        assert len(list((root / "resumes").glob("*"))) == 1


def test_persist_candidate_resume_wraps_managed_copy_failure():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        source = root / "new.txt"
        source.write_text("resume", encoding="utf-8")

        with patch(
            "resume_import_service.store_resume_copy",
            side_effect=OSError("disk full"),
        ):
            try:
                persist_candidate_resume(
                    source,
                    identity=("g1", "Java 工程师"),
                    candidates_path=root / "candidates.json",
                    base_dir=root,
                    imported_at="2026-08-10 10:00:00",
                )
            except ResumeCopyError as exc:
                assert str(exc) == "disk full"
            else:
                raise AssertionError("copy failure should be classified")
