"""Deterministic checkpoint and protocol tests without sockets."""
import hashlib
import tempfile
from pathlib import Path
from unittest.mock import patch

from scripts import release_download as downloader


class Response:
    def __init__(self, data, start, end, *, wrong_range=False, no_range=False):
        self.status_code = 200 if no_range else 206
        self.headers = {"Content-Range": f"bytes {start}-{end}/{len(data) + int(wrong_range)}"}
        self.body = data[start:end+1]

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        pass

    def raise_for_status(self):
        pass

    def iter_content(self, chunk_size):
        yield self.body


class Session:
    def __init__(self, payload, calls, **options):
        self.payload, self.calls, self.options = payload, calls, options

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        pass

    def get(self, url, *, headers, **_kwargs):
        start, end = map(int, headers["Range"][6:].split("-"))
        self.calls.append((start, end, headers))
        return Response(self.payload, start, end, **self.options)


def run_download(path, payload, calls, *, progress=lambda *_args: None, **options):
    remote = dict(size=len(payload), digest="sha256:"+hashlib.sha256(payload).hexdigest(),
                  url="https://example.test/package")
    return downloader.download_segmented(
        remote, path, token="secret", session_factory=lambda: Session(payload, calls, **options),
        progress=progress, workers=2, segment_size=3, attempts=1,
    )


def test_checkpoint_reuses_verified_chunks_and_rejects_corruption_and_changed_release():
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder)/"package"
        calls = []
        def stop(done, total):
            if done == 3:
                raise RuntimeError("stop")
        try:
            run_download(path, b"abcdefghi", calls, progress=stop)
        except RuntimeError as exc:
            assert str(exc) == "stop"
        run_download(path, b"abcdefghi", calls)
        assert len(calls) == 3
        assert all("Authorization" not in headers for _, _, headers in calls)
        (Path(folder)/"package.segments/0.chunk").write_bytes(b"bad")
        run_download(path, b"abcdefghi", calls)
        assert len(calls) == 4
        run_download(path, b"ABCDEFGHI", calls)
        assert len(calls) == 7
        assert path.read_bytes() == b"ABCDEFGHI"


def test_range_protocol_rejects_wrong_total_and_selects_fallback_for_full_response():
    with tempfile.TemporaryDirectory() as folder:
        for option, error in (("wrong_range", RuntimeError), ("no_range", downloader.RangeUnsupported)):
            path = Path(folder)/option
            calls = []
            try:
                run_download(path, b"abcdefghi", calls, **{option: True})
            except error:
                pass
            else:
                raise AssertionError("Invalid range response accepted")
            assert not path.exists()
            assert len(calls) == 1


def test_completed_file_is_not_published_when_global_hash_fails():
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder)/"package"
        remote = dict(size=3, digest="sha256:"+hashlib.sha256(b"abc").hexdigest(),
                      apiUrl="https://example.test/api/asset")
        calls = []
        try:
            downloader.download_segmented(
                remote, path, token="secret", session_factory=lambda: Session(b"bad", calls),
                progress=lambda *_args: None,
            )
        except RuntimeError as exc:
            assert "SHA256 mismatch" in str(exc)
        else:
            raise AssertionError("corrupt package accepted")
        assert not path.exists()
        assert calls[0][2]["Authorization"] == "Bearer secret"


def test_upload_attempt_metrics_include_connection_reset_and_retry():
    import build
    events = []
    class UploadSession:
        def __init__(self):
            self.calls = 0
        def post(self, *_args, **_kwargs):
            self.calls += 1
            if self.calls == 1:
                raise build.requests.exceptions.ConnectionError("reset")
            from unittest.mock import Mock
            return Mock(status_code=201, json=lambda: {"id": 1})
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder)/"test.exe"
        path.write_bytes(b"abc")
        with (
            patch.object(build, "_gitee_session", return_value=UploadSession()),
            patch.object(build, "_gitee_fetch_assets", return_value={}),
            patch.object(build.time, "sleep"),
        ):
            build._gitee_upload_single(path, "https://example.test", "secret", 1, on_event=events.append)
    assert events == ["attempt", "connection_failure", "retry", "attempt"]


def test_release_download_falls_back_only_when_ranges_are_unsupported():
    from scripts import release_ci
    remote = dict(size=3, digest="sha256:"+hashlib.sha256(b"abc").hexdigest(),
                  url="https://example.test/package")
    with tempfile.TemporaryDirectory() as folder:
        directory = Path(folder)
        for failure in (release_ci.release_download.RangeUnsupported("range"), RuntimeError("failed range")):
            path = directory / "package.exe"
            def legacy(_remote, output, **_kwargs):
                output.write_bytes(b"abc")
                return output
            with (
                patch.object(release_ci, "RELEASE_STATE_PATH", directory/"state.json"),
                patch.object(release_ci, "RELEASE_LOG_DIR", directory/"logs"),
                patch.object(release_ci, "_github_access_token", return_value="secret"),
                patch.object(release_ci.release_download, "download_segmented", side_effect=failure),
                patch.object(release_ci, "_download_github_asset_resumable", side_effect=legacy) as fallback,
            ):
                if isinstance(failure, release_ci.release_download.RangeUnsupported):
                    result = release_ci._download_verified_github_artifact(
                        "v2.33.1", path.name, {path.name: remote}, directory, release_sha="a"*40,
                    )
                    assert result.read_bytes() == b"abc"
                    fallback.assert_called_once()
                    path.unlink()
                else:
                    try:
                        release_ci._download_verified_github_artifact(
                            "v2.33.1", path.name, {path.name: remote}, directory, release_sha="a"*40,
                        )
                    except RuntimeError as exc:
                        assert str(exc) == "failed range"
                    else:
                        raise AssertionError("failed range accepted")
                    fallback.assert_not_called()
                    assert release_ci._read_release_state()["artifacts"][path.name]["status"] == "failed"
