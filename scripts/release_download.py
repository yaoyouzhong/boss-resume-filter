"""Bounded Range downloads with persistent, integrity-bound checkpoints."""
from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from collections.abc import Callable

import requests
from urllib3.exceptions import HTTPError as TransportError


class RangeUnsupported(RuntimeError):
    """The server returned a full response instead of the requested range."""


def download_segmented(
    remote: dict, destination: Path, *, token: str,
    session_factory: Callable, progress: Callable[[int, int], None],
    workers: int = 4, segment_size: int = 1024 * 1024,
    attempts: int = 4, segment_timeout: float = 60,
) -> Path:
    """Download fixed ranges; reuse only chunks matching saved identity and hash.

    Each request has a read timeout and a total segment deadline, so a trickling
    connection cannot hold a large package indefinitely. Sessions are per worker
    request, never shared across threads. The caller owns any whole-file fallback.
    """
    size = int(remote["size"])
    digest = str(remote.get("digest") or remote.get("sha256") or "").removeprefix("sha256:").lower()
    if size <= 0 or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
        raise ValueError("Missing file size or SHA256")
    if not 1 <= workers <= 8 or segment_size <= 0 or attempts < 1 or segment_timeout <= 0:
        raise ValueError("Invalid segmented download bounds")
    # Public URLs avoid an extra authenticated asset redirect when available.
    public_url = remote.get("browser_download_url") or remote.get("url")
    url = str(public_url or remote.get("apiUrl") or "")
    if not url:
        raise ValueError("Missing download URL")
    headers = {"Accept": "application/octet-stream", "Accept-Encoding": "identity",
               "User-Agent": "boss-resume-filter-release"}
    if not public_url:
        headers["Authorization"] = f"Bearer {token}"
        headers["X-GitHub-Api-Version"] = "2022-11-28"
    identity = {"size": size, "sha256": digest, "segment_size": segment_size}
    directory = destination.with_name(destination.name + ".segments")
    directory.mkdir(parents=True, exist_ok=True)
    manifest_path = directory / "manifest.json"
    try:
        saved = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        saved = {}
    chunks = saved.get("chunks", {}) if isinstance(saved, dict) and saved.get("identity") == identity else {}
    if not isinstance(chunks, dict):
        chunks = {}
    chunks = dict(chunks)

    def checkpoint() -> None:
        temporary = directory / "manifest.tmp"
        with temporary.open("w", encoding="utf-8") as stream:
            json.dump({"identity": identity, "chunks": chunks}, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, manifest_path)

    ranges = [(start, min(size, start + segment_size) - 1) for start in range(0, size, segment_size)]
    pending = []
    done = 0
    for start, end in ranges:
        part = directory / f"{start}.chunk"
        if (part.is_file() and part.stat().st_size == end - start + 1
                and hashlib.sha256(part.read_bytes()).hexdigest() == chunks.get(str(start))):
            done += end - start + 1
        else:
            chunks.pop(str(start), None)
            pending.append((start, end))
    checkpoint()
    progress(done, size)
    cancel = threading.Event()

    def fetch(bounds: tuple[int, int]) -> tuple[int, bytes]:
        start, end = bounds
        for attempt in range(attempts):
            if cancel.is_set():
                raise RuntimeError("Download cancelled")
            try:
                started = time.monotonic()
                with session_factory() as session:
                    with session.get(
                        url, headers={**headers, "Range": f"bytes={start}-{end}"},
                        stream=True, timeout=(15, min(15, segment_timeout)),
                    ) as response:
                        response.raise_for_status()
                        if response.status_code == 200:
                            raise RangeUnsupported("Server does not support byte ranges")
                        expected_range = f"bytes {start}-{end}/{size}"
                        if response.status_code != 206 or response.headers.get("Content-Range") != expected_range:
                            raise ValueError("Unexpected Content-Range")
                        data = bytearray()
                        read1 = getattr(getattr(response, "raw", None), "read1", None)
                        # read1 returns currently available bytes. A large buffered
                        # read can hide a one-byte trickle beyond the total deadline.
                        blocks = iter(lambda: read1(16 * 1024), b"") if callable(read1) else response.iter_content(chunk_size=1)
                        for block in blocks:
                            if cancel.is_set():
                                raise RuntimeError("Download cancelled")
                            if time.monotonic() - started > segment_timeout:
                                raise requests.exceptions.Timeout("Segment deadline exceeded")
                            data.extend(block)
                            if len(data) > end - start + 1:
                                raise ValueError("Oversized range response")
                        if len(data) != end - start + 1:
                            raise ValueError("Incomplete range response")
                        return start, bytes(data)
            except RangeUnsupported:
                raise
            except (requests.exceptions.RequestException, TransportError, ValueError):
                if attempt + 1 == attempts:
                    # Never propagate signed URLs or Authorization through errors.
                    raise RuntimeError(f"Segment {start}-{end} failed after {attempts} attempts") from None
                print(f"  [分段重试] {destination.name} bytes={start}-{end} attempt={attempt + 2}/{attempts}", flush=True)
                if cancel.wait(min(4, attempt + 1)):
                    raise RuntimeError("Download cancelled") from None
        raise AssertionError("unreachable")

    def save_chunk(result: tuple[int, bytes]) -> None:
        nonlocal done
        start, data = result
        temporary = directory / f"{start}.tmp"
        with temporary.open("wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, directory / f"{start}.chunk")
        chunks[str(start)] = hashlib.sha256(data).hexdigest()
        checkpoint()
        done += len(data)
        progress(done, size)

    # Probe once before starting parallel requests or selecting legacy fallback.
    if pending:
        save_chunk(fetch(pending.pop(0)))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(fetch, bounds) for bounds in pending}
        try:
            for future in as_completed(futures):
                save_chunk(future.result())
                futures.remove(future)
        finally:
            cancel.set()
            for future in futures:
                future.cancel()
    assembled = destination.with_name(destination.name + ".assembling")
    hasher = hashlib.sha256()
    with assembled.open("wb") as stream:
        for start, _end in ranges:
            data = (directory / f"{start}.chunk").read_bytes()
            stream.write(data)
            hasher.update(data)
        stream.flush()
        os.fsync(stream.fileno())
    if hasher.hexdigest() != digest or assembled.stat().st_size != size:
        chunks.clear()
        checkpoint()
        assembled.unlink()
        raise RuntimeError("Complete package SHA256 mismatch; chunks invalidated")
    os.replace(assembled, destination)
    return destination
