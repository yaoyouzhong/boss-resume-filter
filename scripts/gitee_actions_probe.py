"""Probe GitHub-hosted runner uploads to a temporary Gitee Release.

The probe uploads real release-sized artifacts serially, verifies their remote
sizes, and removes the temporary Gitee Release and tag in ``finally``.  It is
deliberately separate from the formal release path: production CI must not be
changed until this network gate succeeds.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

try:
    from requests_toolbelt.multipart.encoder import MultipartEncoder, MultipartEncoderMonitor
except ImportError:  # pragma: no cover - reported explicitly by run_probe
    MultipartEncoder = None
    MultipartEncoderMonitor = None


BASE_DIR = Path(__file__).resolve().parents[1]
STATE_PATH = BASE_DIR / ".release_state.json"
GITEE_OWNER = "yaoyouzhong"
GITEE_REPO = "boss-resume-filter"
GITEE_API = f"https://gitee.com/api/v5/repos/{GITEE_OWNER}/{GITEE_REPO}"
UPLOAD_ATTEMPTS = 3
UPLOAD_STALL_TIMEOUT = 180
PROGRESS_BYTES = 5 * 1024 * 1024
ARTIFACT_NAMES = (
    "BOSS_ResumeFilter.exe",
    "BOSS_ResumeFilter_mac.zip",
    "BOSS_ResumeFilter.dmg",
)


class ProbeError(RuntimeError):
    """The temporary Gitee upload probe failed its contract."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _record_state(tag: str, phase: str, status: str, message: str = "", **extra) -> None:
    """Atomically persist non-secret probe progress for Actions artifacts."""
    current: dict = {}
    if STATE_PATH.exists():
        try:
            current = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            current = {}
    history = list(current.get("history") or [])
    history.append({
        "at": _utc_now(),
        "phase": phase,
        "status": status,
        "message": message,
        **extra,
    })
    data = {
        "kind": "gitee_actions_probe",
        "tag": tag,
        "phase": phase,
        "status": status,
        "message": message,
        "updated_at": _utc_now(),
        "history": history[-100:],
    }
    temp_path = STATE_PATH.with_suffix(".json.tmp")
    temp_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temp_path, STATE_PATH)


def _session() -> requests.Session:
    """Retry only safe queries; large multipart POST retries stay explicit."""
    session = requests.Session()
    retry = Retry(
        total=3,
        backoff_factor=1,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=frozenset({"GET", "HEAD"}),
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    return session


def _sanitize(text: str, token: str) -> str:
    return (text or "").replace(token, "***").replace(quote(token, safe=""), "***")


def _git_push_tag(tag: str, token: str, *, delete: bool = False) -> None:
    """Push or delete one temporary Gitee tag without logging the credential."""
    auth_url = (
        f"https://{GITEE_OWNER}:{quote(token, safe='')}@gitee.com/"
        f"{GITEE_OWNER}/{GITEE_REPO}.git"
    )
    refspec = f":refs/tags/{tag}" if delete else f"refs/tags/{tag}:refs/tags/{tag}"
    result = subprocess.run(
        ["git", "push", auth_url, refspec],
        cwd=BASE_DIR,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode != 0:
        detail = _sanitize(result.stderr or result.stdout, token)
        raise ProbeError(f"Gitee temporary tag {'cleanup' if delete else 'push'} failed: {detail}")


def _create_release(session: requests.Session, tag: str, token: str) -> int:
    response = session.post(
        f"{GITEE_API}/releases",
        params={"access_token": token},
        json={
            "tag_name": tag,
            "name": f"GitHub Actions upload probe {tag}",
            "body": "Temporary upload probe. It will be removed automatically.",
            "target_commitish": tag,
            "prerelease": True,
        },
        timeout=(20, 30),
    )
    response.raise_for_status()
    return int(response.json()["id"])


def _fetch_assets(
    session: requests.Session,
    release_id: int,
    token: str,
) -> dict[str, dict]:
    response = session.get(
        f"{GITEE_API}/releases/{release_id}/attach_files",
        params={"access_token": token},
        timeout=(20, 30),
    )
    response.raise_for_status()
    return {item["name"]: item for item in response.json()}


def _remote_size_match(
    session: requests.Session,
    release_id: int,
    token: str,
    path: Path,
) -> dict | None:
    remote = _fetch_assets(session, release_id, token).get(path.name)
    if not remote:
        return None
    try:
        return remote if int(remote.get("size") or 0) == path.stat().st_size else None
    except (OSError, TypeError, ValueError):
        return None


class _UploadProgress:
    def __init__(self, path: Path, total: int) -> None:
        self.path = path
        self.total = total
        self.started = time.monotonic()
        self.last_reported_bytes = 0
        self.last_reported_at = self.started

    def __call__(self, monitor: MultipartEncoderMonitor) -> None:
        now = time.monotonic()
        if (
            monitor.bytes_read - self.last_reported_bytes < PROGRESS_BYTES
            and now - self.last_reported_at < 15
            and monitor.bytes_read < self.total
        ):
            return
        elapsed = max(now - self.started, 0.001)
        uploaded = min(monitor.bytes_read, self.total)
        percent = uploaded * 100 / self.total
        rate = uploaded / elapsed / 1024 / 1024
        print(
            f"  [upload] {self.path.name}: {uploaded}/{self.total} bytes "
            f"({percent:.1f}%), {elapsed:.1f}s, {rate:.2f} MiB/s",
            flush=True,
        )
        self.last_reported_bytes = monitor.bytes_read
        self.last_reported_at = now


def _upload_one(
    session: requests.Session,
    release_id: int,
    token: str,
    path: Path,
) -> dict:
    """Stream one file with explicit retries and post-timeout verification."""
    for attempt in range(1, UPLOAD_ATTEMPTS + 1):
        started = time.monotonic()
        try:
            with path.open("rb") as source:
                encoder = MultipartEncoder(fields={"file": (path.name, source)})
                monitor = MultipartEncoderMonitor(
                    encoder,
                    _UploadProgress(path, encoder.len),
                )
                response = session.post(
                    f"{GITEE_API}/releases/{release_id}/attach_files",
                    params={"access_token": token},
                    data=monitor,
                    headers={"Content-Type": monitor.content_type},
                    timeout=(20, UPLOAD_STALL_TIMEOUT),
                )
            if 400 <= response.status_code < 500:
                detail = response.text.strip()[:300]
                raise ProbeError(f"Gitee HTTP {response.status_code}: {detail}")
            response.raise_for_status()
            elapsed = time.monotonic() - started
            print(f"  [OK] {path.name} uploaded in {elapsed:.1f}s", flush=True)
            return response.json()
        except ProbeError:
            raise
        except requests.exceptions.RequestException as exc:
            remote = _remote_size_match(session, release_id, token, path)
            if remote:
                elapsed = time.monotonic() - started
                print(
                    f"  [OK] {path.name} accepted by Gitee despite client error "
                    f"after {elapsed:.1f}s",
                    flush=True,
                )
                return remote
            if attempt >= UPLOAD_ATTEMPTS:
                raise ProbeError(
                    f"{path.name} upload failed after {UPLOAD_ATTEMPTS} attempts: "
                    f"{type(exc).__name__}: {exc}"
                ) from exc
            delay = 10 * attempt
            print(
                f"  [retry] {path.name} attempt {attempt}/{UPLOAD_ATTEMPTS} "
                f"failed: {type(exc).__name__}; retrying in {delay}s",
                flush=True,
            )
            time.sleep(delay)
    raise AssertionError("unreachable")


def _delete_release(session: requests.Session, release_id: int, token: str) -> None:
    response = session.delete(
        f"{GITEE_API}/releases/{release_id}",
        params={"access_token": token},
        timeout=(20, 30),
    )
    if response.status_code not in {204, 404}:
        response.raise_for_status()


def run_probe(tag: str, artifact_dir: Path) -> None:
    if MultipartEncoder is None or MultipartEncoderMonitor is None:
        raise ProbeError(
            "requests-toolbelt is missing; install requirements-release.txt"
        )
    token = os.environ.get("GITEE_TOKEN", "")
    if not token:
        raise ProbeError("GITEE_TOKEN is not configured")
    artifacts = [artifact_dir / name for name in ARTIFACT_NAMES]
    missing = [path.name for path in artifacts if not path.is_file() or path.stat().st_size <= 0]
    if missing:
        raise ProbeError("Probe artifacts missing: " + ", ".join(missing))

    session = _session()
    user_response = session.get(
        "https://gitee.com/api/v5/user",
        params={"access_token": token},
        timeout=(20, 30),
    )
    if user_response.status_code != 200:
        raise ProbeError(f"Gitee token validation failed: HTTP {user_response.status_code}")

    release_id: int | None = None
    local_tag_created = False
    remote_tag_pushed = False
    cleanup_errors: list[str] = []
    _record_state(tag, "preflight", "running", "Gitee token validated")
    try:
        subprocess.run(
            ["git", "tag", tag, "HEAD"],
            cwd=BASE_DIR,
            check=True,
            capture_output=True,
            text=True,
        )
        local_tag_created = True
        _git_push_tag(tag, token)
        remote_tag_pushed = True
        _record_state(tag, "tag", "complete", "Temporary tag pushed")

        release_id = _create_release(session, tag, token)
        _record_state(tag, "release", "complete", "Temporary prerelease created")

        timings: dict[str, float] = {}
        for path in artifacts:
            started = time.monotonic()
            _record_state(tag, "upload", "running", path.name)
            _upload_one(session, release_id, token, path)
            timings[path.name] = round(time.monotonic() - started, 1)
            _record_state(
                tag,
                "upload",
                "complete",
                path.name,
                seconds=timings[path.name],
                size=path.stat().st_size,
            )

        remote = _fetch_assets(session, release_id, token)
        mismatches = [
            path.name
            for path in artifacts
            if int((remote.get(path.name) or {}).get("size") or 0) != path.stat().st_size
        ]
        if mismatches:
            raise ProbeError("Gitee size verification failed: " + ", ".join(mismatches))
        total = round(sum(timings.values()), 1)
        _record_state(
            tag,
            "verify",
            "complete",
            "All three artifacts verified",
            seconds=total,
            timings=timings,
        )
        print(f"[OK] GitHub Actions -> Gitee probe passed in {total:.1f}s", flush=True)
    except Exception as exc:
        _record_state(tag, "probe", "failed", f"{type(exc).__name__}: {exc}")
        raise
    finally:
        if release_id is not None:
            try:
                _delete_release(session, release_id, token)
            except Exception as exc:
                cleanup_errors.append(f"release cleanup: {type(exc).__name__}: {exc}")
        if remote_tag_pushed:
            try:
                _git_push_tag(tag, token, delete=True)
            except Exception as exc:
                cleanup_errors.append(f"remote tag cleanup: {type(exc).__name__}: {exc}")
        if local_tag_created:
            subprocess.run(
                ["git", "tag", "-d", tag],
                cwd=BASE_DIR,
                check=False,
                capture_output=True,
                text=True,
            )
        if cleanup_errors:
            _record_state(tag, "cleanup", "failed", "; ".join(cleanup_errors))
            raise ProbeError("; ".join(cleanup_errors))
        _record_state(tag, "cleanup", "complete", "Temporary Release and tag removed")
        print("[OK] Temporary Gitee Release and tag removed", flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="GitHub Actions to Gitee upload probe")
    parser.add_argument("--tag", required=True)
    parser.add_argument("--artifact-dir", type=Path, required=True)
    args = parser.parse_args()
    try:
        run_probe(args.tag, args.artifact_dir.resolve())
    except (ProbeError, requests.exceptions.RequestException, subprocess.CalledProcessError) as exc:
        print(f"[ERROR] {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
