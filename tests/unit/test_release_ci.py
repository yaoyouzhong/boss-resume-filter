import importlib.util
from pathlib import Path
import tempfile
from contextlib import contextmanager
from unittest.mock import patch

BASE_DIR = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "boss_resume_filter_release_ci",
    BASE_DIR / "scripts" / "release_ci.py",
)
assert SPEC and SPEC.loader
release_ci = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(release_ci)


def _asset(size: int = 123) -> dict:
    return {"size": size, "digest": "sha256:" + "a" * 64}


@contextmanager
def _raises(error_type, message: str):
    try:
        yield
    except error_type as exc:
        assert message in str(exc)
    else:
        raise AssertionError(f"expected {error_type.__name__}: {message}")


def test_release_authorization_must_match_version_exactly():
    release_ci.validate_authorization("2.21", "正式发布 v2.21")

    with _raises(release_ci.ReleaseAutomationError, "发布授权不匹配"):
        release_ci.validate_authorization("2.21", "release 2.21")


def test_resume_rejects_new_business_changes_after_the_release_commit():
    with (
        patch.object(release_ci, "_is_ancestor", return_value=True),
        patch.object(
            release_ci,
            "_changed_paths",
            return_value={"latest.json", "gui_main.py"},
        ),
    ):
        with _raises(release_ci.ReleaseAutomationError, "新的业务变更"):
            release_ci._assert_resume_head_compatible("a" * 40, "b" * 40)


def test_resume_allows_only_the_post_release_manifest_commit():
    with (
        patch.object(release_ci, "_is_ancestor", return_value=True),
        patch.object(release_ci, "_changed_paths", return_value={"latest.json"}),
    ):
        release_ci._assert_resume_head_compatible("a" * 40, "b" * 40)


def test_prepare_reuses_complete_remote_artifacts_on_same_commit_resume():
    with tempfile.TemporaryDirectory() as temp_dir:
        output = Path(temp_dir) / "github-output.txt"
        remote_assets = {
            "BOSS_ResumeFilter.exe": _asset(),
            "BOSS_ResumeFilter_mac.zip": _asset(),
            "BOSS_ResumeFilter.dmg": _asset(),
        }
        with (
            patch.object(release_ci, "resolve_release_sha", return_value=("a" * 40, True)),
            patch.object(
                release_ci.build,
                "_extract_changelog_release",
                return_value=("v2.21 — Test", "### 新增功能\n\n- Test"),
            ),
            patch.object(release_ci.build, "_preflight_checks") as preflight,
            patch.object(
                release_ci.build,
                "_get_github_release_assets",
                return_value=remote_assets,
            ),
        ):
            result = release_ci.prepare_release(
                "2.21",
                "正式发布 v2.21",
                dry_run=True,
                github_output=str(output),
            )

        assert result["resume"] == "true"
        assert result["needs_windows"] == "false"
        assert result["needs_macos"] == "false"
        preflight.assert_called_once_with(require_clean=False, strict_changelog=True)
        written = output.read_text(encoding="utf-8")
        assert "release_sha=" + "a" * 40 in written
        assert "needs_windows=false" in written
        assert "needs_macos=false" in written


def test_publish_exposes_release_only_after_both_stores_are_complete():
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)
        notes_path = temp_path / "notes.md"
        notes_path.write_text("notes", encoding="utf-8")
        artifacts = [temp_path / name for name in release_ci.RELEASE_ARTIFACTS]
        for artifact in artifacts:
            artifact.write_bytes(b"artifact")
        github_assets = {path.name: _asset(path.stat().st_size) for path in artifacts}
        events: list[str] = []

        with (
            patch.object(release_ci, "_require_publish_secrets", return_value="token"),
            patch.object(release_ci, "_git_text", return_value="a" * 40),
            patch.object(release_ci, "_version_at_ref", return_value="2.21"),
            patch.object(
                release_ci,
                "_fetch_and_assert_current_master_compatible",
                side_effect=lambda *_: events.append("master_safe") or "a" * 40,
            ),
            patch.object(release_ci, "_ensure_gitee_remote"),
            patch.object(
                release_ci.build,
                "_extract_changelog_release",
                return_value=("v2.21 — Test", "### 新增功能\n\n- Test"),
            ),
            patch.object(release_ci, "_notes_file", return_value=notes_path),
            patch.object(release_ci, "_ensure_origin_tag", side_effect=lambda *_: events.append("tag")),
            patch.object(release_ci, "_ensure_gitee_tag", side_effect=lambda *_: events.append("gitee_tag")),
            patch.object(
                release_ci,
                "_ensure_github_release",
                side_effect=lambda *_: events.append("github_draft"),
            ),
            patch.object(release_ci, "_ensure_local_artifacts", return_value=artifacts),
            patch.object(
                release_ci,
                "_upload_github_artifacts",
                side_effect=lambda *_: events.append("github_assets") or github_assets,
            ),
            patch.object(
                release_ci,
                "_publish_gitee_artifacts",
                side_effect=lambda *_: events.append("gitee_assets")
                or release_ci._canonical_downloads_cn("2.21"),
            ),
            patch.object(
                release_ci,
                "_publish_github_release",
                side_effect=lambda *_: events.append("github_public"),
            ),
            patch.object(
                release_ci,
                "_commit_and_sync_manifest",
                side_effect=lambda *_: events.append("manifest") or "b" * 40,
            ),
            patch.object(
                release_ci.build,
                "_verify_release_remote_state",
                side_effect=lambda *_: events.append("remote_verify") or True,
            ),
            patch.object(
                release_ci,
                "verify_public_endpoints",
                side_effect=lambda *_: events.append("public_verify"),
            ),
        ):
            release_ci.publish_release("2.21", "正式发布 v2.21", "a" * 40)

        assert events.index("github_assets") < events.index("github_public")
        assert events.index("gitee_assets") < events.index("github_public")
        assert events.count("master_safe") == 2
        assert events.index("gitee_assets") < events.index("master_safe", 1)
        assert events.index("master_safe", 1) < events.index("github_public")
        assert events.index("github_public") < events.index("manifest")
        assert events[-2:] == ["remote_verify", "public_verify"]


def test_gitee_ci_upload_accepts_windows_and_macos_artifacts_together():
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)
        artifacts = [temp_path / name for name in release_ci.RELEASE_ARTIFACTS]
        for artifact in artifacts:
            artifact.write_bytes(b"artifact")
        cache = {
            "token": "token",
            "owner": "owner",
            "repo": "repo",
            "tag": "v2.21",
            "api_base": "https://example.invalid/api",
            "release_id": 1,
            "existing": {},
        }

        def fake_upload(path, *_args, **_kwargs):
            return path.name, {
                "browser_download_url": f"https://example.invalid/{path.name}"
            }

        with patch.object(
            release_ci.build,
            "_gitee_upload_single",
            side_effect=fake_upload,
        ):
            downloads = release_ci.build._gitee_upload_artifacts(
                "2.21",
                "v2.21 — Test",
                "notes",
                artifacts,
                release_cache=cache,
                large_workers=1,
            )

        assert set(downloads) == {"windows", "macos", "macos_dmg"}


def test_gitee_ci_upload_stops_after_the_first_failed_artifact():
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)
        artifacts = [temp_path / name for name in release_ci.RELEASE_ARTIFACTS]
        for artifact in artifacts:
            artifact.write_bytes(b"artifact")
        cache = {
            "token": "token",
            "owner": "owner",
            "repo": "repo",
            "tag": "v2.21",
            "api_base": "https://example.invalid/api",
            "release_id": 1,
            "existing": {},
        }
        uploaded: list[str] = []

        def fail_first(path, *_args, **_kwargs):
            uploaded.append(path.name)
            raise RuntimeError("upload failed")

        with (
            patch.object(
                release_ci.build,
                "_gitee_upload_single",
                side_effect=fail_first,
            ),
            _raises(RuntimeError, "upload failed"),
        ):
            release_ci.build._gitee_upload_artifacts(
                "2.21",
                "v2.21 — Test",
                "notes",
                artifacts,
                release_cache=cache,
                large_workers=1,
                fail_fast=True,
            )

        assert uploaded == ["BOSS_ResumeFilter.exe"]
