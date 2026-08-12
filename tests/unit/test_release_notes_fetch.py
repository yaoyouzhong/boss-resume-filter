import io
import json
import tempfile
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

import updater


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


def test_request_get_uses_direct_session_only_for_gitee():
    with patch.object(
        updater.requests,
        "get",
        side_effect=lambda url, **kwargs: (url, kwargs),
    ):
        gitee = updater._request_get("https://gitee.com/owner/repo/file", timeout=3)
        github = updater._request_get("https://github.com/owner/repo/file", timeout=4)

    assert gitee[1]["proxies"] == {"http": "", "https": "", "all": ""}
    assert "proxies" not in github[1]


def test_download_file_streams_chunks_and_reports_progress():
    class DownloadResponse:
        headers = {"content-length": "5"}

        def raise_for_status(self):
            return None

        def iter_content(self, chunk_size):
            assert chunk_size == 8192
            return iter((b"ab", b"", b"cde"))

    progress = []
    with tempfile.TemporaryDirectory() as tmpdir:
        destination = Path(tmpdir) / "update.exe"
        with patch.object(updater.requests, "get", return_value=DownloadResponse()):
            success, error = updater.download_file(
                "https://example.test/update.exe",
                destination,
                progress_callback=lambda downloaded, total: progress.append((downloaded, total)),
            )

        assert destination.read_bytes() == b"abcde"

    assert success is True
    assert error is None
    assert progress == [(2, 5), (5, 5)]


def test_download_file_failure_removes_partial_destination():
    with tempfile.TemporaryDirectory() as tmpdir:
        destination = Path(tmpdir) / "update.exe"
        destination.write_bytes(b"partial")
        with patch.object(
            updater.requests,
            "get",
            side_effect=updater.requests.exceptions.Timeout("network timeout"),
        ):
            success, error = updater.download_file("https://example.test/update.exe", destination)

        assert not destination.exists()

    assert success is False
    assert "network timeout" in error


def test_download_and_verify_removes_file_when_integrity_check_fails():
    with tempfile.TemporaryDirectory() as tmpdir:
        destination = Path(tmpdir) / "update.exe"

        def fake_download(_url, path, _callback):
            Path(path).write_bytes(b"MZbroken")
            return True, None

        with (
            patch.object(updater, "download_file", side_effect=fake_download),
            patch.object(updater, "verify_downloaded_file", return_value=(False, "SHA256 mismatch")),
        ):
            success, error = updater.download_and_verify_file(
                "https://example.test/update.exe",
                destination,
                asset_info={"sha256": "0" * 64},
            )

        assert not destination.exists()

    assert success is False
    assert error == "SHA256 mismatch"


def test_get_cached_release_notes_returns_fresh_current_version():
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        cache = tmp_path / "release_notes_cache.json"
        cache.write_text(
            json.dumps({
                "version": "2.11.2",
                "fetched_at": 990,
                "release_notes": "### 新增功能\n\n- 远端说明",
            }, ensure_ascii=False),
            encoding="utf-8",
        )

        with patch.object(updater.time, "time", return_value=1000):
            notes = updater.get_cached_release_notes("v2.11.2", base_dir=tmp_path)

        assert "远端说明" in notes


def test_fetch_current_release_notes_uses_gitee_latest_json():
    calls = []

    def fake_get(url, **kwargs):
        calls.append((url, kwargs))
        return FakeResponse({
            "version": "2.11.2",
            "release_notes": "### 新增功能\n\n- 当前版本远端说明",
        })

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        with patch.object(updater.requests, "get", side_effect=fake_get):
            notes = updater.fetch_current_release_notes("2.11.2", use_cache=False, base_dir=tmp_path)

        assert "当前版本远端说明" in notes
        assert len(calls) == 1
        assert calls[0][1]["timeout"] == updater.UPDATE_TIMEOUT_RELEASE_NOTES_GITEE
        cached = json.loads((tmp_path / "release_notes_cache.json").read_text(encoding="utf-8"))
        assert cached["source"] == "gitee"


def test_fetch_current_release_notes_retries_gitee_before_github():
    calls = []

    def fake_get(url, **kwargs):
        calls.append((url, kwargs))
        if len(calls) == 1:
            raise updater.requests.exceptions.Timeout("slow")
        return FakeResponse({
            "version": "2.11.2",
            "release_notes": "### 体验优化\n\n- Gitee 重试成功",
        })

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        with patch.object(updater.requests, "get", side_effect=fake_get):
            notes = updater.fetch_current_release_notes("2.11.2", use_cache=False, base_dir=tmp_path)

        assert "Gitee 重试成功" in notes
        assert len(calls) == 2
        assert "gitee.com" in calls[0][0]
        assert "gitee.com" in calls[1][0]
        assert calls[0][1]["timeout"] == updater.UPDATE_TIMEOUT_RELEASE_NOTES_GITEE
        assert calls[1][1]["timeout"] == updater.UPDATE_TIMEOUT_RELEASE_NOTES_GITEE_RETRY


def test_fetch_current_release_notes_falls_back_to_github_tag():
    calls = []

    def fake_get(url, **kwargs):
        calls.append((url, kwargs))
        if "gitee.com" in url and len(calls) <= 2:
            raise updater.requests.exceptions.Timeout("slow")
        return FakeResponse({"body": "### 问题修复\n\n- GitHub 说明"})

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        with patch.object(updater.requests, "get", side_effect=fake_get):
            notes = updater.fetch_current_release_notes("2.11.2", use_cache=False, base_dir=tmp_path)

        assert "GitHub 说明" in notes
        assert len(calls) == 3
        assert calls[2][0].endswith("/releases/tags/v2.11.2")
        assert calls[2][1]["timeout"] == updater.UPDATE_TIMEOUT_RELEASE_NOTES_GITHUB


def test_check_gitee_latest_retries_once_on_timeout():
    calls = []

    def fake_get(url, **kwargs):
        calls.append((url, kwargs))
        if len(calls) == 1:
            raise updater.requests.exceptions.Timeout("cold raw host")
        return FakeResponse({
            "version": "2.11.2",
            "release_notes": "无更新",
        })

    with patch.object(updater, "get_current_version", return_value="2.11.2"):
        with patch.object(updater.requests, "get", side_effect=fake_get):
            result = updater.check_gitee_latest()

    assert result["error"] is None
    assert result["latest"] == "2.11.2"
    assert len(calls) == 2
    assert calls[0][1]["timeout"] == updater.UPDATE_TIMEOUT_GITEE
    assert calls[1][1]["timeout"] == updater.UPDATE_TIMEOUT_GITEE


def test_check_gitee_latest_detects_same_version_binary_replacement():
    payload = {
        "version": "2.27",
        "release_notes": "内容更新",
        "downloads": {"windows": "https://github.example/app.exe"},
        "downloads_cn": {"windows": "https://gitee.example/app.exe"},
        "assets": {"windows": {"sha256": "B" * 64, "size": 123}},
    }

    with (
        patch.object(updater, "get_current_version", return_value="2.27"),
        patch.object(updater, "_get_gitee_latest_response", return_value=FakeResponse(payload)),
        patch.object(updater, "_get_current_exe_sha256", return_value="A" * 64),
        patch.object(updater.sys, "platform", "win32"),
    ):
        result = updater.check_gitee_latest()

    assert result["has_update"] is True
    assert result["update_type"] == "content"
    assert result["content_changed"] is True
    assert result["download_url"] == "https://gitee.example/app.exe"
    assert result["download_url_fallback"] == "https://github.example/app.exe"
    assert result["asset_info"]["sha256"] == "B" * 64


def test_check_github_release_uses_release_digest_for_integrity():
    download_url = (
        "https://github.com/example/repo/releases/download/v9.9.9/"
        "BOSS_ResumeFilter.exe"
    )
    release = {
        "tag_name": "v9.9.9",
        "assets": [{
            "name": "BOSS_ResumeFilter.exe",
            "size": 123,
            "digest": "sha256:" + "A" * 64,
            "browser_download_url": download_url,
        }],
    }

    with (
        patch.object(updater, "get_current_version", return_value="1.0"),
        patch.object(updater.sys, "platform", "win32"),
        patch.object(updater.requests, "get", return_value=FakeResponse(release)) as get,
    ):
        result = updater.check_github_release("example/repo")

    assert result["error"] is None
    assert result["has_update"] is True
    assert result["asset_info"] == {"size": 123, "sha256": "a" * 64}
    get.assert_called_once()


def test_check_github_release_uses_matching_latest_manifest_when_digest_missing():
    download_url = (
        "https://github.com/example/repo/releases/download/v9.9.9/"
        "BOSS_ResumeFilter.exe"
    )
    release = {
        "tag_name": "v9.9.9",
        "assets": [{
            "name": "BOSS_ResumeFilter.exe",
            "size": 123,
            "browser_download_url": download_url,
        }],
    }
    manifest = {
        "version": "9.9.9",
        "downloads": {"windows": download_url},
        "assets": {"windows": {"size": 123, "sha256": "b" * 64}},
    }

    with (
        patch.object(updater, "get_current_version", return_value="1.0"),
        patch.object(updater.sys, "platform", "win32"),
        patch.object(
            updater.requests,
            "get",
            side_effect=[FakeResponse(release), FakeResponse(manifest)],
        ) as get,
    ):
        result = updater.check_github_release("example/repo")

    assert result["error"] is None
    assert result["asset_info"] == {"size": 123, "sha256": "b" * 64}
    assert get.call_args_list[1].args[0].endswith("/example/repo/master/latest.json")


def test_check_github_release_rejects_mismatched_integrity_manifest():
    download_url = (
        "https://github.com/example/repo/releases/download/v9.9.9/"
        "BOSS_ResumeFilter.exe"
    )
    release = {
        "tag_name": "v9.9.9",
        "assets": [{
            "name": "BOSS_ResumeFilter.exe",
            "size": 123,
            "browser_download_url": download_url,
        }],
    }
    stale_manifest = {
        "version": "9.9.8",
        "downloads": {"windows": download_url},
        "assets": {"windows": {"size": 123, "sha256": "b" * 64}},
    }

    with (
        patch.object(updater, "get_current_version", return_value="1.0"),
        patch.object(updater.sys, "platform", "win32"),
        patch.object(
            updater.requests,
            "get",
            side_effect=[FakeResponse(release), FakeResponse(stale_manifest)],
        ),
    ):
        result = updater.check_github_release("example/repo")

    assert result["has_update"] is True
    assert "完整性清单版本" in result["error"]
    assert result["asset_info"] == {}


def test_silent_update_check_does_not_print_gitee_fallback():
    class ImmediateThread:
        def __init__(self, target, daemon=False):
            self.target = target

        def start(self):
            self.target()

    class ImmediateRoot:
        def after(self, delay, callback):
            callback()

    gitee_result = {
        "latest": None,
        "current": "2.11.2",
        "has_update": False,
        "error": "Gitee 连接超时",
    }
    github_result = {
        "latest": "2.11.2",
        "current": "2.11.2",
        "has_update": False,
        "error": None,
    }
    completed = []

    with patch.object(updater, "check_gitee_latest", return_value=gitee_result):
        with patch.object(updater, "check_github_release", return_value=github_result):
            with patch.object(updater.threading, "Thread", ImmediateThread):
                output = io.StringIO()
                with redirect_stdout(output):
                    updater.check_and_update_gui(
                        ImmediateRoot(),
                        silent=True,
                        on_complete=lambda result: completed.append(result),
                        source="startup",
                    )

    assert output.getvalue() == ""
    assert completed == [github_result]
