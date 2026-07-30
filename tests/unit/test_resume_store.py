"""Managed resume storage regression tests."""

import tempfile
from pathlib import Path

from resume_store import (
    UnmanagedResumePathError,
    delete_managed_resume,
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
