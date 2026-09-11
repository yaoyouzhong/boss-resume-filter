"""Real local HTTP checks for release range downloads; no external network."""
import hashlib
import json
import re
import tempfile
import threading
import time
import sys
from collections import Counter
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import requests
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from scripts import release_download


@contextmanager
def server(*, mode="normal"):
    payload = bytes(range(256)) * 1024
    counts = Counter()
    active = [0, 0]
    lock = threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_GET(self):
            start, end = map(int, re.fullmatch(r"bytes=(\d+)-(\d+)", self.headers["Range"]).groups())
            with lock:
                counts[start] += 1
                attempt = counts[start]
                active[0] += 1
                active[1] = max(active)
            try:
                self.send_response(200 if mode == "no_range" else 206)
                self.send_header("Content-Range", f"bytes {start}-{end}/{len(payload)}")
                self.send_header("Content-Length", str(end-start+1))
                self.end_headers()
                if mode == "disconnect" and start == 65536 and attempt == 1:
                    self.wfile.write(payload[start:start+20])
                    self.close_connection = True
                    return
                if mode == "slow":
                    for byte in payload[start:end+1]:
                        self.wfile.write(bytes([byte]))
                        self.wfile.flush()
                        time.sleep(0.02)
                    return
                time.sleep(0.02)
                body = payload[start:end+1]
                self.wfile.write(b"x" * len(body) if mode == "corrupt" else body)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass
            finally:
                with lock:
                    active[0] -= 1

    http = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=http.serve_forever, daemon=True)
    thread.start()
    remote = dict(size=len(payload), digest="sha256:"+hashlib.sha256(payload).hexdigest(),
                  url=f"http://127.0.0.1:{http.server_port}/package")
    try:
        yield payload, remote, counts, active
    finally:
        http.shutdown()
        http.server_close()
        thread.join()


def session():
    result = requests.Session()
    result.trust_env = False
    return result


def download(remote, path, **kwargs):
    return release_download.download_segmented(
        remote, path, token="unused", session_factory=session,
        progress=kwargs.pop("progress", lambda *_args: None), workers=3,
        segment_size=65536, **kwargs,
    )


def test_segmented_download_bounds_parallelism_and_retries_only_failed_range():
    with server(mode="disconnect") as (payload, remote, counts, active), tempfile.TemporaryDirectory() as folder:
        path = Path(folder)/"package.exe"
        download(remote, path)
        assert path.read_bytes() == payload
        assert counts == {0: 1, 65536: 2, 131072: 1, 196608: 1}
        assert 2 <= active[1] <= 3


def test_segmented_download_restarts_from_checkpoint_and_rejects_corrupt_cached_chunk():
    with server() as (payload, remote, counts, _active), tempfile.TemporaryDirectory() as folder:
        path = Path(folder)/"package.exe"
        def interrupt(done, _total):
            if done:
                raise RuntimeError("simulated process stop")
        try:
            download(remote, path, progress=interrupt)
        except RuntimeError as exc:
            assert str(exc) == "simulated process stop"
        download(remote, path)
        assert counts[0] == 1
        chunk = path.with_name(path.name+".segments")/"0.chunk"
        chunk.write_bytes(b"x"*65536)
        download(remote, path)
        assert counts[0] == 2
        assert counts[65536] == 1
        assert path.read_bytes() == payload


def test_segmented_download_rejects_whole_file_hash_mismatch():
    with server(mode="corrupt") as (_payload, remote, _counts, _active), tempfile.TemporaryDirectory() as folder:
        path = Path(folder)/"package.exe"
        try:
            download(remote, path)
        except RuntimeError as exc:
            assert "SHA256 mismatch" in str(exc)
        else:
            raise AssertionError("corrupt package accepted")
        assert not path.exists()
        assert json.loads((Path(folder)/"package.exe.segments/manifest.json").read_text())["chunks"] == {}


def test_segmented_download_detects_range_unsupported_before_parallel_requests():
    with server(mode="no_range") as (_payload, remote, counts, _active), tempfile.TemporaryDirectory() as folder:
        try:
            download(remote, Path(folder)/"package.exe")
        except release_download.RangeUnsupported:
            pass
        else:
            raise AssertionError("full response accepted as a segment")
        assert counts == {0: 1}


def test_segmented_download_bounds_trickling_connection_wall_time():
    with server(mode="slow") as (_payload, remote, _counts, _active), tempfile.TemporaryDirectory() as folder:
        started = time.monotonic()
        try:
            download(remote, Path(folder)/"package.exe", attempts=1, segment_timeout=0.12)
        except RuntimeError as exc:
            assert "failed after 1 attempts" in str(exc)
        else:
            raise AssertionError("slow stream was not stopped")
        assert time.monotonic()-started < 2


if __name__ == "__main__":
    for name, function in sorted(list(globals().items())):
        if name.startswith("test_"):
            function()
    print("LOCAL_HTTP_RANGE_CHECKS_OK")
