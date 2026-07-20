import importlib.util
import hashlib
import json
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


def _review() -> dict:
    return {
        "release_title": "v2.21 — Test",
        "release_body": "### 新增功能\n\n- **Test**：Description",
        "content_sha": "c" * 64,
        "review_warnings": [],
    }


@contextmanager
def _raises(error_type, message: str):
    try:
        yield
    except error_type as exc:
        assert message in str(exc)
    else:
        raise AssertionError(f"expected {error_type.__name__}: {message}")


def test_release_authorization_must_match_version_exactly():
    release_ci.validate_authorization("2.21", "确认正式发布 v2.21")

    with _raises(release_ci.ReleaseAutomationError, "发布授权不匹配"):
        release_ci.validate_authorization("2.21", "release 2.21")


def test_origin_tag_configures_git_identity_before_creating_annotated_tag():
    calls: list[list[str]] = []
    with (
        patch.object(release_ci.build, "_remote_tag_commit", return_value=None),
        patch.object(release_ci, "_commit_for_ref", return_value=None),
        patch.object(
            release_ci,
            "_run",
            side_effect=lambda args, **_kwargs: (
                calls.append(args)
                or release_ci.subprocess.CompletedProcess(args, 0, "", "")
            ),
        ),
    ):
        release_ci._ensure_origin_tag(
            "v2.21",
            "a" * 40,
            Path("release-notes.md"),
        )

    tag_index = next(i for i, args in enumerate(calls) if args[:2] == ["git", "tag"])
    assert calls[tag_index - 2] == [
        "git", "config", "user.name", "github-actions[bot]",
    ]
    assert calls[tag_index - 1] == [
        "git", "config", "user.email",
        "41898282+github-actions[bot]@users.noreply.github.com",
    ]
    assert calls[tag_index + 1] == ["git", "push", "origin", "refs/tags/v2.21"]


def test_working_tree_paths_preserves_first_path_character():
    result = release_ci.subprocess.CompletedProcess(
        ["git", "status", "--porcelain"],
        0,
        stdout=" M latest.json\n",
        stderr="",
    )
    with patch.object(release_ci, "_run", return_value=result) as run:
        paths = release_ci._working_tree_paths()

    assert paths == {"latest.json"}
    run.assert_called_once_with(
        ["git", "status", "--porcelain"],
        capture_output=True,
    )


def test_local_release_tag_is_fetched_when_missing():
    calls: list[list[str]] = []
    release_sha = "a" * 40
    with (
        patch.object(release_ci, "_commit_for_ref", side_effect=[None, release_sha]),
        patch.object(
            release_ci,
            "_run",
            side_effect=lambda args, **_kwargs: (
                calls.append(args)
                or release_ci.subprocess.CompletedProcess(args, 0, "", "")
            ),
        ),
    ):
        release_ci._ensure_local_release_tag("v2.21", release_sha)

    assert calls == [[
        "git", "fetch", "origin", "refs/tags/v2.21:refs/tags/v2.21",
    ]]


def test_release_state_is_atomic_and_contains_no_credentials():
    with tempfile.TemporaryDirectory() as temp_dir:
        state_path = Path(temp_dir) / ".release_state.json"
        with patch.object(release_ci, "RELEASE_STATE_PATH", state_path):
            release_ci._write_release_state(
                "2.21",
                "a" * 40,
                "download_github_artifacts",
                "in_progress",
                artifact="BOSS_ResumeFilter.exe",
                artifact_status="downloading",
                downloaded_bytes=10,
                expected_bytes=20,
            )
        state = json.loads(state_path.read_text(encoding="utf-8"))

    assert state["phase"] == "download_github_artifacts"
    assert state["artifacts"]["BOSS_ResumeFilter.exe"]["downloaded_bytes"] == 10
    assert "token" not in json.dumps(state).lower()


def test_release_state_retries_three_windows_sharing_violations():
    busy = OSError("file busy")
    busy.winerror = 32
    replace_calls = 0
    real_replace = release_ci.os.replace

    def flaky_replace(source, destination):
        nonlocal replace_calls
        replace_calls += 1
        if replace_calls <= 3:
            raise busy
        return real_replace(source, destination)

    with tempfile.TemporaryDirectory() as temp_dir:
        state_path = Path(temp_dir) / ".release_state.json"
        with (
            patch.object(release_ci, "RELEASE_STATE_PATH", state_path),
            patch.object(release_ci.os, "replace", side_effect=flaky_replace),
            patch.object(release_ci.release_retry.time, "sleep") as sleep,
        ):
            release_ci._write_release_state(
                "2.21", "a" * 40, "public_verification", "in_progress"
            )

        assert state_path.exists()

    assert replace_calls == 4
    assert [item.args[0] for item in sleep.call_args_list] == [0.2, 0.5, 1.0]


def test_publish_github_release_accepts_lost_response_when_state_changed():
    failed = release_ci.subprocess.CompletedProcess(
        ["gh"], 1, stdout="", stderr="connection reset"
    )
    with (
        patch.object(
            release_ci.build,
            "_get_github_release_info",
            side_effect=[{"isDraft": True}, {"isDraft": False}],
        ),
        patch.object(release_ci, "_run", return_value=failed) as run,
        patch.object(release_ci.release_retry.time, "sleep") as sleep,
    ):
        release_ci._publish_github_release("v2.21")

    run.assert_called_once()
    sleep.assert_not_called()


def test_github_release_query_retries_transient_cli_failure():
    failed = release_ci.subprocess.CompletedProcess(
        ["gh"], 1, stdout="", stderr="temporary network failure",
    )
    succeeded = release_ci.subprocess.CompletedProcess(
        ["gh"], 0, stdout=json.dumps({"assets": []}), stderr="",
    )
    with (
        patch.object(release_ci.build.subprocess, "run", side_effect=[failed, succeeded]) as run,
        patch.object(release_ci.build.time, "sleep") as sleep,
    ):
        result = release_ci.build._github_release_view_json("v2.21", "assets")

    assert result == {"assets": []}
    assert run.call_count == 2
    sleep.assert_called_once_with(2)


def test_github_asset_download_resumes_after_stall_and_verifies_sha256():
    payload = b"abcdef"
    remote = {
        "name": "BOSS_ResumeFilter.exe",
        "size": len(payload),
        "digest": "sha256:" + hashlib.sha256(payload).hexdigest(),
        "apiUrl": "https://api.github.invalid/assets/1",
    }

    class FakeResponse:
        def __init__(self, chunks, *, status_code=200, headers=None, fail=False):
            self.chunks = chunks
            self.status_code = status_code
            self.headers = headers or {}
            self.fail = fail

        def raise_for_status(self):
            return None

        def iter_content(self, chunk_size):
            del chunk_size
            yield from self.chunks
            if self.fail:
                raise release_ci.build.requests.exceptions.ReadTimeout("stalled")

        def close(self):
            return None

    class FakeSession:
        def __init__(self):
            self.headers = []
            self.responses = [
                FakeResponse([payload[:3]], fail=True),
                FakeResponse(
                    [payload[3:]],
                    status_code=206,
                    headers={"Content-Range": "bytes 3-5/6"},
                ),
            ]

        def get(self, _url, *, headers, **_kwargs):
            self.headers.append(dict(headers))
            return self.responses.pop(0)

    with tempfile.TemporaryDirectory() as temp_dir:
        directory = Path(temp_dir)
        destination = directory / remote["name"]
        session = FakeSession()
        with (
            patch.object(release_ci, "RELEASE_STATE_PATH", directory / "state.json"),
            patch.object(release_ci.time, "sleep"),
        ):
            result = release_ci._download_github_asset_resumable(
                remote,
                destination,
                version="2.21",
                release_sha="a" * 40,
                token="secret",
                session=session,
            )

        assert result.read_bytes() == payload
        assert "Range" not in session.headers[0]
        assert session.headers[1]["Range"] == "bytes=3-"
        assert not destination.with_name(destination.name + ".part").exists()


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
            patch.dict(
                release_ci.os.environ,
                {
                    "GITHUB_ACTIONS": "true",
                    "GITHUB_EVENT_NAME": "workflow_dispatch",
                    "GITHUB_REF_NAME": "master",
                },
            ),
            patch.object(release_ci, "resolve_release_sha", return_value=("a" * 40, True)),
            patch.object(
                release_ci.release_content_review,
                "review_release_content",
                return_value=_review(),
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
                "确认正式发布 v2.21",
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


def test_prepare_rejects_non_manual_github_actions_event():
    with patch.dict(
        release_ci.os.environ,
        {
            "GITHUB_ACTIONS": "true",
            "GITHUB_EVENT_NAME": "pull_request",
            "GITHUB_REF_NAME": "master",
        },
    ):
        with _raises(release_ci.ReleaseAutomationError, "只能由 workflow_dispatch 手动触发"):
            release_ci.prepare_release(
                "2.21",
                "确认正式发布 v2.21",
                dry_run=True,
            )


def test_stage_github_stops_after_draft_and_artifacts_are_complete():
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
            patch.dict(
                release_ci.os.environ,
                {
                    "GITHUB_ACTIONS": "true",
                    "GITHUB_EVENT_NAME": "workflow_dispatch",
                    "GITHUB_REF_NAME": "master",
                    "GH_TOKEN": "token",
                },
            ),
            patch.object(release_ci, "_git_text", return_value="a" * 40),
            patch.object(release_ci, "_version_at_ref", return_value="2.21"),
            patch.object(
                release_ci,
                "_fetch_and_assert_current_master_compatible",
                side_effect=lambda *_: events.append("master_safe") or "a" * 40,
            ),
            patch.object(
                release_ci.release_content_review,
                "review_release_content",
                return_value=_review(),
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
            patch.object(release_ci, "_publish_gitee_artifacts") as gitee_publish,
            patch.object(release_ci, "_publish_github_release") as github_publish,
        ):
            release_ci.stage_github_release(
                "2.21", "确认正式发布 v2.21", "a" * 40, "c" * 64,
            )

        assert events == ["master_safe", "tag", "github_draft", "github_assets"]
        gitee_publish.assert_not_called()
        github_publish.assert_not_called()


def test_confirmed_local_preview_reuses_gate_only_for_the_same_content():
    with (
        patch.dict(release_ci.os.environ, {"GITHUB_ACTIONS": "false"}),
        patch.object(release_ci, "resolve_release_sha", return_value=("a" * 40, False)),
        patch.object(
            release_ci.release_content_review,
            "review_release_content",
            return_value=_review(),
        ),
        patch.object(release_ci.build, "_preflight_checks") as preflight,
        patch.object(release_ci.build, "_get_github_release_assets", return_value={}),
    ):
        release_ci.prepare_release(
            "2.21",
            "确认正式发布 v2.21",
            approved_content_sha="c" * 64,
            dry_run=True,
            reuse_reviewed_gate=True,
        )
    preflight.assert_not_called()

def test_finalize_local_publishes_github_before_gitee_mirror():
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)
        artifacts = [temp_path / name for name in release_ci.RELEASE_ARTIFACTS]
        for artifact in artifacts:
            artifact.write_bytes(b"artifact")
        github_assets = {path.name: _asset(path.stat().st_size) for path in artifacts}
        events: list[str] = []

        def fake_git_text(*args):
            if args == ("branch", "--show-current"):
                return "master"
            if args == ("rev-parse", "HEAD"):
                return "a" * 40
            raise AssertionError(args)

        with (
            patch.object(release_ci, "RELEASE_STATE_PATH", temp_path / "state.json"),
            patch.object(release_ci, "require_local_gitee_access", return_value="token"),
            patch.object(release_ci, "_ensure_gitee_remote"),
            patch.object(release_ci, "_working_tree_paths", return_value=set()),
            patch.object(release_ci, "_git_text", side_effect=fake_git_text),
            patch.object(release_ci, "_version_at_ref", return_value="2.21"),
            patch.object(
                release_ci,
                "_fetch_and_assert_current_master_compatible",
                side_effect=lambda *_: events.append("master_safe") or "a" * 40,
            ),
            patch.object(release_ci.build, "_remote_tag_commit", return_value="a" * 40),
            patch.object(
                release_ci.release_content_review,
                "review_release_content",
                return_value=_review(),
            ),
            patch.object(release_ci.build, "_get_github_release_info", return_value={"isDraft": True}),
            patch.object(
                release_ci.build,
                "_verify_github_release_assets_complete",
                return_value=github_assets,
            ),
            patch.object(
                release_ci,
                "_download_verified_github_artifacts",
                side_effect=lambda *_args, **_kwargs: events.append("download_verify") or artifacts,
            ),
            patch.object(release_ci, "_ensure_gitee_tag", side_effect=lambda *_: events.append("gitee_tag")),
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
            release_ci.finalize_release_local(
                "2.21", "确认正式发布 v2.21", "a" * 40, "c" * 64,
            )

        assert events.count("master_safe") == 2
        assert events.index("download_verify") < events.index("github_public")
        assert events.index("github_public") < events.index("gitee_tag")
        assert events.index("gitee_tag") < events.index("gitee_assets")
        assert events.index("gitee_assets") < events.index("manifest")
        assert events[-2:] == ["remote_verify", "public_verify"]


def test_gitee_large_upload_is_rejected_on_github_actions():
    with patch.dict(release_ci.os.environ, {"GITHUB_ACTIONS": "true", "GITEE_TOKEN": "token"}):
        with _raises(release_ci.ReleaseAutomationError, "禁止在 GitHub Actions 中上传"):
            release_ci.require_local_gitee_access()


def test_gitee_same_value_push_race_is_treated_as_idempotent_success():
    failed = release_ci.subprocess.CompletedProcess(
        ["git", "push"], 1, stdout="", stderr="incorrect old value provided",
    )
    with (
        patch.object(release_ci, "_run", return_value=failed),
        patch.object(
            release_ci.build,
            "_remote_ref_commit",
            side_effect=["a" * 40, "b" * 40],
        ),
    ):
        release_ci._sanitized_git_push(
            "https://token.invalid/repo.git",
            "HEAD:refs/heads/master",
            "token",
            remote_ref="refs/heads/master",
            expected_commit="b" * 40,
        )


def test_local_gitee_resume_reuses_existing_same_size_staged_assets():
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)
        artifacts = [temp_path / name for name in release_ci.RELEASE_ARTIFACTS]
        for artifact in artifacts:
            artifact.write_bytes(b"artifact")
        github_assets = {path.name: _asset(path.stat().st_size) for path in artifacts}
        cache = {
            "existing": {
                path.name: {"size": path.stat().st_size}
                for path in artifacts
            }
        }

        with (
            patch.object(release_ci.build, "_gitee_get_release_cache", return_value=cache),
            patch.object(
                release_ci.build,
                "_github_asset_matches_local",
                return_value=(True, "SHA256 一致"),
            ),
            patch.object(release_ci.build, "_gitee_upload_artifacts") as upload,
            patch.object(
                release_ci.build,
                "_verify_gitee_release_assets_complete",
                return_value=True,
            ),
        ):
            downloads = release_ci._publish_gitee_artifacts(
                "2.21",
                "v2.21 — Test",
                "notes",
                artifacts,
                github_assets,
            )

    upload.assert_not_called()
    assert downloads == release_ci._canonical_downloads_cn("2.21")


def test_gitee_local_upload_accepts_windows_and_macos_artifacts_together():
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


def test_gitee_local_upload_stops_after_the_first_failed_artifact():
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
