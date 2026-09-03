"""Deterministic release staging and finalization contracts.

The GitHub Actions workflow calls this module in two hosted phases:

``prepare``
    Validate the explicit release authorization, resolve the immutable release
    commit, run the strict project gate, and decide which platform artifacts
    still need building.

``stage-github``
    Create or reuse the immutable GitHub tag, keep the GitHub Release as a
    Draft, upload and verify the version-specific release artifacts, then stop.

The local release driver calls ``finalize-local`` after hosted staging.  That
phase publishes the verified GitHub Release, downloads the same artifacts for
the local Gitee mirror, updates ``latest.json`` on both remotes, and performs
public acceptance checks.

The implementation deliberately reuses ``build.py`` for version, changelog,
artifact, upload, and integrity contracts.  YAML remains orchestration only.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from urllib.parse import quote


BASE_DIR = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = Path(__file__).resolve().parent
for import_path in (BASE_DIR, SCRIPTS_DIR):
    if str(import_path) not in sys.path:
        sys.path.insert(0, str(import_path))

import build  # noqa: E402
import release_content_review  # noqa: E402
import release_retry  # noqa: E402
from subprocess_utils import hidden_subprocess  # noqa: E402

subprocess = hidden_subprocess(subprocess)


RELEASE_ARTIFACTS = (
    "BOSS_ResumeFilter.exe",
    "BOSS_ResumeFilter_mac.zip",
    "BOSS_ResumeFilter.dmg",
)
RESUME_ONLY_MASTER_CHANGES = frozenset({"latest.json"})
GITEE_OWNER = "yaoyouzhong"
GITEE_REPO = "boss-resume-filter"
RELEASE_STATE_PATH = BASE_DIR / ".release_state.json"
RELEASE_STATE_SCHEMA = 3
RELEASE_LOG_DIR = BASE_DIR / "logs"
DOWNLOAD_CHUNK_SIZE = 1024 * 1024
DOWNLOAD_CONNECT_TIMEOUT = 15
DOWNLOAD_STALL_TIMEOUT = 45
DOWNLOAD_ATTEMPTS = 4
DEFAULT_GITEE_UPLOAD_WORKERS = 1


def _release_artifacts(version_or_tag: str) -> tuple[str, ...]:
    """Return the release artifact contract for one public version."""
    return build._release_asset_names_for_version(version_or_tag)


def _windows_release_artifacts(version_or_tag: str) -> tuple[str, ...]:
    """Return Windows artifacts, including the standalone tool from v2.32."""
    return tuple(
        name
        for key, name in build._release_asset_items(version_or_tag)
        if key in {"windows", "education_windows"}
    )


def _macos_release_artifacts(version_or_tag: str) -> tuple[str, ...]:
    """Return macOS installer and update artifacts."""
    return tuple(
        name
        for key, name in build._release_asset_items(version_or_tag)
        if key in {"macos", "macos_dmg"}
    )


class ReleaseAutomationError(RuntimeError):
    """A deterministic release contract was not satisfied."""


def _fail(message: str) -> None:
    raise ReleaseAutomationError(message)


def _read_release_state() -> dict:
    try:
        data = json.loads(RELEASE_STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_release_state(
    version: str,
    release_sha: str,
    phase: str,
    status: str,
    *,
    artifact: str = "",
    artifact_status: str = "",
    gitee_status: str = "",
    downloaded_bytes: int | None = None,
    expected_bytes: int | None = None,
    error_type: str = "",
    error_message: str = "",
    details: dict | None = None,
    actions_run: dict | None = None,
) -> None:
    """Atomically persist safe checkpoints, phase timings, and diagnostics."""
    previous = _read_release_state()
    if (
        previous.get("version") != version
        or previous.get("release_sha") != release_sha
    ):
        previous = {}
    now = datetime.now()
    now_text = now.isoformat(timespec="seconds")
    previous_phase = previous.get("phase")
    previous_status = previous.get("status")
    state = {
        **previous,
        "schema": RELEASE_STATE_SCHEMA,
        "version": version,
        "tag": f"v{version}",
        "release_sha": release_sha,
        "phase": phase,
        "status": status,
        "started_at": previous.get("started_at") or now_text,
        "updated_at": now_text,
    }

    phases = dict(state.get("phases") or {})
    phase_state = dict(phases.get(phase) or {})
    phase_started = (
        status == "in_progress"
        and (previous_phase != phase or previous_status != "in_progress")
    )
    if phase_started:
        phase_state["started_at"] = now_text
        phase_state["attempts"] = int(phase_state.get("attempts") or 0) + 1
        phase_state.pop("finished_at", None)
        phase_state.pop("duration_seconds", None)
        phase_state.pop("error_type", None)
        phase_state.pop("error_message", None)
    phase_state["status"] = status
    if status in {"complete", "failed"}:
        phase_state["finished_at"] = now_text
        started_text = str(phase_state.get("started_at") or now_text)
        try:
            started = datetime.fromisoformat(started_text)
        except ValueError:
            started = now
        phase_state["duration_seconds"] = round(
            max(0.0, (now - started).total_seconds()),
            3,
        )
    if details:
        phase_state["details"] = {
            **dict(phase_state.get("details") or {}),
            **details,
        }
    if error_type:
        state["error_type"] = error_type
        phase_state["error_type"] = error_type
    else:
        state.pop("error_type", None)
        if status != "failed":
            phase_state.pop("error_type", None)
    if error_message:
        safe_error = release_retry.redact_sensitive_text(error_message)
        state["error_message"] = safe_error
        phase_state["error_message"] = safe_error
    else:
        state.pop("error_message", None)
        if status != "failed":
            phase_state.pop("error_message", None)
    if actions_run:
        safe_run = {
            key: actions_run.get(key)
            for key in (
                "databaseId",
                "url",
                "status",
                "conclusion",
                "createdAt",
                "startedAt",
                "updatedAt",
                "headSha",
            )
            if actions_run.get(key) is not None
        }
        state["actions_run"] = safe_run
        phase_state["actions_run"] = safe_run
    if artifact:
        artifacts = dict(state.get("artifacts") or {})
        item = dict(artifacts.get(artifact) or {})
        if artifact_status:
            item["status"] = artifact_status
            item["download_status"] = artifact_status
        if gitee_status:
            item["gitee_status"] = gitee_status
        if downloaded_bytes is not None:
            item["downloaded_bytes"] = max(0, int(downloaded_bytes))
        if expected_bytes is not None:
            item["expected_bytes"] = max(0, int(expected_bytes))
        artifacts[artifact] = item
        state["artifacts"] = artifacts
    phases[phase] = phase_state
    state["phases"] = phases
    if status == "complete" and phase == "public_verification":
        state["completed_at"] = now_text
        try:
            run_started = datetime.fromisoformat(str(state["started_at"]))
        except ValueError:
            run_started = now
        state["total_duration_seconds"] = round(
            max(0.0, (now - run_started).total_seconds()),
            3,
        )

    temp_path = RELEASE_STATE_PATH.with_suffix(RELEASE_STATE_PATH.suffix + ".tmp")
    temp_path.write_text(
        json.dumps(state, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    for attempt in range(len(release_retry.FILE_RETRY_DELAYS) + 1):
        try:
            os.replace(temp_path, RELEASE_STATE_PATH)
            break
        except OSError as exc:
            transient = getattr(exc, "winerror", None) in {5, 32, 33}
            if not transient or attempt >= len(release_retry.FILE_RETRY_DELAYS):
                raise
            delay = release_retry.FILE_RETRY_DELAYS[attempt]
            print(
                "  [重试] 写入本机发布状态时文件被临时占用，准备第 "
                f"{attempt + 1}/{len(release_retry.FILE_RETRY_DELAYS)} 次重试"
                f"（{delay:g}s 后）"
            )
            release_retry.time.sleep(delay)
    event_changed = (
        previous_phase != phase
        or previous_status != status
        or bool(error_type)
        or bool(artifact_status)
        or bool(gitee_status)
        or bool(actions_run)
    )
    if event_changed:
        _append_release_event(
            version,
            now_text,
            phase,
            status,
            phase_state,
            artifact=artifact,
            artifact_status=artifact_status,
            gitee_status=gitee_status,
        )


def _append_release_event(
    version: str,
    timestamp: str,
    phase: str,
    status: str,
    phase_state: dict,
    *,
    artifact: str = "",
    artifact_status: str = "",
    gitee_status: str = "",
) -> None:
    """Append one credential-free, human-readable release event."""
    parts = [f"[{timestamp}]", phase, status]
    if artifact:
        parts.append(f"artifact={artifact}")
    if artifact_status:
        parts.append(f"artifact_status={artifact_status}")
    if gitee_status:
        parts.append(f"gitee_status={gitee_status}")
    if status in {"complete", "failed"}:
        parts.append(f"duration={phase_state.get('duration_seconds', 0):g}s")
    if phase_state.get("error_type"):
        parts.append(f"error_type={phase_state['error_type']}")
    if phase_state.get("error_message"):
        parts.append(f"error={phase_state['error_message']}")
    try:
        log_dir = (
            RELEASE_LOG_DIR
            if RELEASE_STATE_PATH.parent == BASE_DIR
            else RELEASE_STATE_PATH.parent / "logs"
        )
        log_dir.mkdir(parents=True, exist_ok=True)
        with open(
            log_dir / f"release-v{version}.log",
            "a",
            encoding="utf-8",
        ) as output:
            output.write(" ".join(parts) + "\n")
    except OSError as exc:
        print(f"  [WARN] 发布事件日志写入失败: {type(exc).__name__}")


def record_actions_run(version: str, release_sha: str, run: dict) -> None:
    """Persist the exact hosted run used by this release or resume."""
    run_status = str(run.get("status") or "").lower()
    conclusion = str(run.get("conclusion") or "").lower()
    if run_status == "completed":
        status = "complete" if conclusion == "success" else "failed"
    else:
        status = "in_progress"
    _write_release_state(
        version,
        release_sha,
        "github_actions",
        status,
        actions_run=run,
        details={"conclusion": run.get("conclusion")},
    )


def _print_release_timing_summary(version: str, release_sha: str) -> None:
    """Print the persisted timing ledger for one release attempt."""
    state = _read_release_state()
    if (
        state.get("version") != version
        or state.get("release_sha") != release_sha
    ):
        return
    print("\n>>> 发布阶段耗时")
    for name, item in (state.get("phases") or {}).items():
        status = item.get("status") or "unknown"
        duration = float(item.get("duration_seconds") or 0)
        attempts = int(item.get("attempts") or 0)
        print(
            f"  - {name}: {status}, {duration:.1f}s, "
            f"尝试 {attempts} 次"
        )
    if state.get("total_duration_seconds") is not None:
        print(f"  总耗时: {float(state['total_duration_seconds']):.1f}s")
    print(f"  详细日志: logs/release-v{version}.log")


def _report_previous_release_state(version: str, release_sha: str) -> None:
    state = _read_release_state()
    if (
        state.get("version") == version
        and state.get("release_sha") == release_sha
        and not (
            state.get("phase") == "public_verification"
            and state.get("status") == "complete"
        )
    ):
        print(
            "  [续跑] 上次本机发布停在 "
            f"{state.get('phase', 'unknown')} / {state.get('status', 'unknown')}"
        )


def _completed_phase_details(
    version: str,
    release_sha: str,
    phase: str,
) -> dict:
    """Return saved details only for a completed phase of the same release."""
    state = _read_release_state()
    if (
        state.get("version") != version
        or state.get("release_sha") != release_sha
    ):
        return {}
    phase_state = (state.get("phases") or {}).get(phase) or {}
    if phase_state.get("status") != "complete":
        return {}
    return dict(phase_state.get("details") or {})


def _run(
    args: list[str],
    *,
    check: bool = True,
    capture_output: bool = False,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        cwd=BASE_DIR,
        check=check,
        capture_output=capture_output,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
    )


def _git_text(*args: str) -> str:
    result = _run(["git", *args], capture_output=True)
    return result.stdout.strip()


def _run_external(
    args: list[str],
    label: str,
    *,
    postcondition: Callable[[], bool] | None = None,
) -> subprocess.CompletedProcess[str]:
    result = release_retry.run_cli_with_retries(
        _run,
        args,
        label,
        postcondition=postcondition,
    )
    if result.returncode != 0:
        _fail(f"{label}失败：{release_retry.command_detail(result)}")
    return result


def _configure_git_identity() -> None:
    """Configure the repository-local identity used by release automation."""
    _run(["git", "config", "user.name", "github-actions[bot]"])
    _run([
        "git", "config", "user.email",
        "41898282+github-actions[bot]@users.noreply.github.com",
    ])


def _working_tree_paths() -> set[str]:
    """Return porcelain paths without stripping their status prefix."""
    result = _run(
        ["git", "status", "--porcelain"],
        capture_output=True,
    )
    paths: set[str] = set()
    for line in result.stdout.splitlines():
        if not line.strip():
            continue
        if len(line) < 4:
            _fail(f"无法解析 git status 输出：{line!r}")
        paths.add(line[3:].split(" -> ", 1)[-1].replace("\\", "/"))
    return paths


def _normalize_version(value: str) -> str:
    version = str(value or "").strip().removeprefix("v")
    build._validate_version_format(version)
    return version


def expected_authorization(version: str) -> str:
    return f"确认正式发布 v{version}"


def validate_authorization(version: str, authorization: str) -> None:
    expected = expected_authorization(version)
    if authorization != expected:
        _fail(f"发布授权不匹配：必须准确填写 {expected!r}")


def _version_at_ref(ref: str) -> str:
    source = _git_text("show", f"{ref}:gui_main.py")
    match = re.search(r'^__version__\s*=\s*["\']([^"\']+)["\']', source, re.MULTILINE)
    if not match:
        _fail(f"无法从 {ref[:12]} 的 gui_main.py 读取版本号")
    return match.group(1)


def _is_ancestor(ancestor: str, descendant: str) -> bool:
    result = _run(
        ["git", "merge-base", "--is-ancestor", ancestor, descendant],
        check=False,
        capture_output=True,
    )
    return result.returncode == 0


def _changed_paths(base: str, head: str) -> set[str]:
    output = _git_text("diff", "--name-only", f"{base}..{head}")
    return {line.strip().replace("\\", "/") for line in output.splitlines() if line.strip()}


def _assert_resume_head_compatible(release_sha: str, master_sha: str) -> None:
    if release_sha == master_sha:
        return
    if not _is_ancestor(release_sha, master_sha):
        _fail("当前 master 不是发布提交的后继，拒绝续跑")
    changed = _changed_paths(release_sha, master_sha)
    unexpected = sorted(changed - RESUME_ONLY_MASTER_CHANGES)
    if unexpected:
        _fail(
            "同版本续跑期间 master 已包含新的业务变更，拒绝复用旧标签："
            + ", ".join(unexpected)
        )


def resolve_release_sha(version: str) -> tuple[str, bool]:
    """Resolve the release commit, allowing only a manifest-only resume."""
    head_sha = _git_text("rev-parse", "HEAD")
    origin_master = build._remote_ref_commit("origin", "refs/heads/master")
    if not origin_master:
        _fail("无法读取 origin/master")
    if head_sha != origin_master:
        _fail("正式发布只能从当前 origin/master 提交触发")

    tag = f"v{version}"
    tag_sha = build._remote_tag_commit("origin", tag)
    release_sha = tag_sha or head_sha
    if tag_sha:
        _assert_resume_head_compatible(tag_sha, head_sha)

    source_version = _version_at_ref(release_sha)
    if source_version != version:
        _fail(
            f"发布提交版本为 {source_version!r}，与请求版本 {version!r} 不一致"
        )
    return release_sha, bool(tag_sha)


def _asset_has_integrity(asset: dict | None) -> bool:
    if not asset:
        return False
    try:
        size = int(asset.get("size") or 0)
    except (TypeError, ValueError):
        return False
    return size > 0 and bool(build._asset_digest_sha256(asset))


def _write_github_outputs(path: str | None, values: dict[str, str]) -> None:
    output_path = path or os.environ.get("GITHUB_OUTPUT")
    if not output_path:
        return
    with open(output_path, "a", encoding="utf-8") as output:
        for key, value in values.items():
            output.write(f"{key}={value}\n")


def prepare_release(
    version: str,
    authorization: str,
    *,
    approved_content_sha: str = "",
    dry_run: bool = False,
    reuse_reviewed_gate: bool = False,
    github_output: str | None = None,
) -> dict[str, str]:
    """Run the strict, mutation-free release preparation gate."""
    version = _normalize_version(version)
    validate_authorization(version, authorization)

    if os.environ.get("GITHUB_ACTIONS") == "true":
        if os.environ.get("GITHUB_EVENT_NAME") != "workflow_dispatch":
            _fail("正式发布工作流只能由 workflow_dispatch 手动触发")
        if os.environ.get("GITHUB_REF_NAME") != "master":
            _fail("正式发布工作流只能从 master 触发")

    release_sha, resume = resolve_release_sha(version)
    review = release_content_review.review_release_content(version, release_sha)
    if not dry_run or reuse_reviewed_gate:
        release_content_review.require_approved_content(review, approved_content_sha)
    release_title = review["release_title"]

    # A local dry run may inspect an intentionally dirty implementation branch.
    # Hosted dry runs still start from a clean checkout, but do not need a
    # special code path.
    if reuse_reviewed_gate:
        if not dry_run:
            _fail("复用内容审核门禁只允许用于本机无副作用预检")
        print(f"  [OK] 复用已确认的本机严格门禁: {review['content_sha'][:12]}")
    else:
        build._preflight_checks(
            require_clean=not dry_run,
            strict_changelog=True,
        )

    tag = f"v{version}"
    remote_assets = build._get_github_release_assets(tag) if resume else {}
    needs_windows = not all(
        _asset_has_integrity(remote_assets.get(name))
        for name in _windows_release_artifacts(version)
    )
    needs_macos = not all(
        _asset_has_integrity(remote_assets.get(name))
        for name in _macos_release_artifacts(version)
    )

    outputs = {
        "version": version,
        "tag": tag,
        "release_sha": release_sha,
        "resume": str(resume).lower(),
        "needs_windows": str(needs_windows).lower(),
        "needs_macos": str(needs_macos).lower(),
        "content_sha": review["content_sha"],
    }
    _write_github_outputs(github_output, outputs)
    result = {
        **outputs,
        "release_title": review["release_title"],
        "release_body": review["release_body"],
    }

    mode = "断点续跑" if resume else "首次发布"
    print(f"\n[OK] {tag} 发布准备通过（{mode}）")
    print(f"  发布提交: {release_sha}")
    print(f"  Release 标题: {release_title}")
    print(f"  内容凭证: {review['content_sha'][:12]}")
    print(f"  Windows 构建: {'需要' if needs_windows else '复用'}")
    print(f"  macOS 构建: {'需要' if needs_macos else '复用'}")
    if dry_run:
        print("  Dry Run：未创建标签、未上传附件、未发布")
    return result


def _notes_file(title: str, body: str) -> Path:
    handle = tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        suffix=".md",
        prefix="release_notes_",
        delete=False,
    )
    try:
        handle.write(f"{title}\n\n{body}\n")
    finally:
        handle.close()
    return Path(handle.name)


def _commit_for_ref(ref: str) -> str | None:
    result = _run(
        ["git", "rev-list", "-n", "1", ref],
        check=False,
        capture_output=True,
    )
    return (result.stdout.strip() or None) if result.returncode == 0 else None


def _ensure_origin_tag(tag: str, release_sha: str, notes_path: Path) -> None:
    remote_sha = build._remote_tag_commit("origin", tag)
    if remote_sha and remote_sha != release_sha:
        _fail(f"origin/{tag} 指向其他提交，禁止移动公开标签")

    local_sha = _commit_for_ref(tag)
    if local_sha and local_sha != release_sha:
        _fail(f"本地 {tag} 指向其他提交，禁止覆盖")
    if not local_sha:
        _configure_git_identity()
        _run(["git", "tag", "-a", tag, release_sha, "-F", str(notes_path)])
        print(f"  [OK] 已创建 annotated tag: {tag}")

    if not remote_sha:
        _run_external(
            ["git", "push", "origin", f"refs/tags/{tag}"],
            f"推送 GitHub tag {tag}",
            postcondition=lambda: build._remote_tag_commit("origin", tag)
            == release_sha,
        )
        print(f"  [OK] 已推送 GitHub tag: {tag}")
    else:
        print(f"  [跳过] GitHub tag 已存在且提交一致: {tag}")


def _require_github_publish_secret() -> None:
    if not (os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")):
        _fail("缺少 GH_TOKEN/GITHUB_TOKEN，无法发布 GitHub Release")


def require_local_gitee_access() -> str:
    """Require a usable local Gitee token and reject hosted-runner uploads."""
    if os.environ.get("GITHUB_ACTIONS") == "true":
        _fail("Gitee Release 大文件只能从本机镜像，禁止在 GitHub Actions 中上传")
    token = os.environ.get("GITEE_TOKEN", "")
    if not token:
        _fail("本机缺少 GITEE_TOKEN，无法完成 Gitee 镜像")
    if not build._gitee_ping(token):
        _fail("本机无法访问 Gitee API，未触发云端构建")
    return token


def _sanitized_git_push(
    url: str,
    refspec: str,
    token: str,
    *,
    remote_ref: str = "",
    expected_commit: str = "",
) -> None:
    if remote_ref and expected_commit:
        if build._remote_ref_commit("gitee", remote_ref) == expected_commit:
            print(f"  [跳过] Gitee {remote_ref} 已是目标提交")
            return
    result = release_retry.run_cli_with_retries(
        _run,
        ["git", "push", url, refspec],
        "同步 Gitee 引用",
        postcondition=(
            lambda: build._remote_ref_commit("gitee", remote_ref)
            == expected_commit
        )
        if remote_ref and expected_commit
        else None,
    )
    if result.returncode == 0:
        return
    if remote_ref and expected_commit:
        if build._remote_ref_commit("gitee", remote_ref) == expected_commit:
            print(f"  [OK] Gitee {remote_ref} 同值竞态已自动收敛")
            return
    encoded = quote(token, safe="")
    detail = (result.stderr or result.stdout or "git push failed")
    detail = detail.replace(token, "***").replace(encoded, "***")
    _fail(f"Gitee 推送失败：{detail.strip()}")


def _gitee_authenticated_url(token: str) -> str:
    encoded = quote(token, safe="")
    return f"https://{GITEE_OWNER}:{encoded}@gitee.com/{GITEE_OWNER}/{GITEE_REPO}.git"


def _ensure_gitee_remote() -> None:
    """Ensure hosted runners have the public Gitee remote used by verifiers."""
    expected = f"https://gitee.com/{GITEE_OWNER}/{GITEE_REPO}.git"
    current = _run(
        ["git", "remote", "get-url", "gitee"],
        check=False,
        capture_output=True,
    )
    if current.returncode != 0:
        _run(["git", "remote", "add", "gitee", expected])
        return
    url = current.stdout.strip().removesuffix("/")
    accepted_suffix = f"gitee.com/{GITEE_OWNER}/{GITEE_REPO}.git"
    if not url.endswith(accepted_suffix):
        _fail(f"gitee remote 指向非预期仓库：{url}")


def _ensure_local_release_tag(tag: str, release_sha: str) -> None:
    """Fetch the immutable GitHub tag when the local clone does not have it."""
    local_sha = _commit_for_ref(tag)
    if local_sha and local_sha != release_sha:
        _fail(f"本地 {tag} 指向其他提交，禁止覆盖")
    if not local_sha:
        _run_external(
            [
                "git",
                "fetch",
                "origin",
                f"refs/tags/{tag}:refs/tags/{tag}",
            ],
            f"拉取 GitHub tag {tag}",
        )
        local_sha = _commit_for_ref(tag)
        if local_sha != release_sha:
            _fail(f"自动拉取 {tag} 后提交校验失败")
        print(f"  [OK] 已自动拉取 GitHub tag: {tag}")


def _ensure_gitee_tag(tag: str, release_sha: str, token: str) -> None:
    _ensure_local_release_tag(tag, release_sha)
    remote_sha = build._remote_tag_commit("gitee", tag)
    if remote_sha and remote_sha != release_sha:
        _fail(f"gitee/{tag} 指向其他提交，禁止移动公开标签")
    if remote_sha:
        print(f"  [跳过] Gitee tag 已存在且提交一致: {tag}")
        return
    _sanitized_git_push(
        _gitee_authenticated_url(token),
        f"refs/tags/{tag}:refs/tags/{tag}",
        token,
        remote_ref=f"refs/tags/{tag}",
        expected_commit=release_sha,
    )
    verified = build._remote_tag_commit("gitee", tag)
    if verified != release_sha:
        _fail("Gitee tag 推送后校验失败")
    print(f"  [OK] 已推送 Gitee tag: {tag}")


def _ensure_github_release(tag: str, title: str, body: str) -> dict | None:
    notes_path = _notes_file(title, body)
    body_path = notes_path.with_name(notes_path.stem + "_body.md")
    body_path.write_text(body + "\n", encoding="utf-8")
    try:
        info = build._get_github_release_info(tag)
        if info is None:
            _run_external(
                [
                    "gh", "release", "create", tag,
                    "--draft",
                    "--verify-tag",
                    "--title", title,
                    "--notes-file", str(body_path),
                ],
                f"创建 GitHub Draft Release {tag}",
                postcondition=lambda: build._get_github_release_info(tag)
                is not None,
            )
            print(f"  [OK] 已创建 GitHub Draft Release: {tag}")
            return build._get_github_release_info(tag)

        if info.get("tagName") != tag:
            _fail("GitHub Release 的标签与请求不一致")
        _run_external(
            [
                "gh", "release", "edit", tag,
                "--title", title,
                "--notes-file", str(body_path),
            ],
            f"更新 GitHub Release {tag}",
            postcondition=lambda: (
                (current := build._get_github_release_info(tag)) is not None
                and current.get("name") == title
                and str(current.get("body") or "").strip() == body.strip()
            ),
        )
        print(f"  [跳过] GitHub Release 已存在，已同步标题和说明: {tag}")
        return build._get_github_release_info(tag)
    finally:
        notes_path.unlink(missing_ok=True)
        body_path.unlink(missing_ok=True)


def _ensure_local_artifacts(
    tag: str,
    artifact_dir: Path | None = None,
) -> list[Path]:
    artifact_dir = artifact_dir or build.DIST_DIR
    artifact_dir.mkdir(parents=True, exist_ok=True)
    remote_assets = build._get_github_release_assets(tag)
    paths: list[Path] = []
    for name in _release_artifacts(tag):
        path = artifact_dir / name
        if not path.exists() and name in remote_assets:
            print(f"  [复用] 从现有 GitHub Release 下载: {name}")
            build._download_from_github_release(tag, name, artifact_dir)
        if not path.exists() or path.stat().st_size <= 0:
            _fail(f"发布产物缺失或为空：{name}")
        paths.append(path)
    return paths


def _github_access_token() -> str:
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if token:
        return token
    result = _run(["gh", "auth", "token"], check=False, capture_output=True)
    token = result.stdout.strip() if result.returncode == 0 else ""
    if not token:
        _fail("无法读取 GitHub 登录凭证，不能下载 Draft Release 附件")
    return token


def _download_github_asset_resumable(
    remote: dict,
    destination: Path,
    *,
    version: str,
    release_sha: str,
    token: str | None = None,
    session=None,
    attempts: int = DOWNLOAD_ATTEMPTS,
    connect_timeout: int = DOWNLOAD_CONNECT_TIMEOUT,
    stall_timeout: int = DOWNLOAD_STALL_TIMEOUT,
    state_phase: str = "download_github_artifacts",
) -> Path:
    """Download one GitHub asset with Range resume and inactivity timeout."""
    name = str(remote.get("name") or destination.name)
    try:
        expected_size = int(remote.get("size") or 0)
    except (TypeError, ValueError):
        expected_size = 0
    expected_sha = build._asset_digest_sha256(remote)
    url = str(remote.get("apiUrl") or remote.get("url") or "")
    if expected_size <= 0 or not expected_sha or not url:
        _fail(f"GitHub 附件缺少续传或完整性元数据：{name}")

    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_name(destination.name + ".part")
    token = token or _github_access_token()
    session = session or build.requests.Session()
    last_error = "unknown"

    for attempt in range(1, attempts + 1):
        offset = partial.stat().st_size if partial.exists() else 0
        if offset > expected_size:
            partial.write_bytes(b"")
            offset = 0

        if offset == expected_size and offset > 0:
            if build._sha256_file(partial) == expected_sha:
                os.replace(partial, destination)
                _write_release_state(
                    version,
                    release_sha,
                    state_phase,
                    "in_progress",
                    artifact=name,
                    artifact_status="complete",
                    downloaded_bytes=expected_size,
                    expected_bytes=expected_size,
                )
                return destination
            partial.write_bytes(b"")
            offset = 0

        headers = {
            "Accept": "application/octet-stream",
            "Authorization": f"Bearer {token}",
            "User-Agent": "boss-resume-filter-release",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if offset:
            headers["Range"] = f"bytes={offset}-"
            print(f"  [续传] {name}: {_format_bytes(offset)} / {_format_bytes(expected_size)}")
        else:
            print(f"  [下载] {name}: {_format_bytes(expected_size)}")

        _write_release_state(
            version,
            release_sha,
            state_phase,
            "in_progress",
            artifact=name,
            artifact_status="downloading",
            downloaded_bytes=offset,
            expected_bytes=expected_size,
        )
        response = None
        try:
            response = session.get(
                url,
                headers=headers,
                stream=True,
                allow_redirects=True,
                timeout=(connect_timeout, stall_timeout),
            )
            if offset and response.status_code == 416 and offset == expected_size:
                continue
            response.raise_for_status()

            mode = "ab"
            if offset:
                content_range = str(response.headers.get("Content-Range") or "")
                if response.status_code != 206 or not content_range.startswith(
                    f"bytes {offset}-"
                ):
                    print(f"  [重下] {name}: 下载端不支持当前续传位置")
                    mode = "wb"
                    offset = 0
            else:
                mode = "wb"

            downloaded = offset
            last_report = time.monotonic()
            with open(partial, mode) as output:
                for chunk in response.iter_content(chunk_size=DOWNLOAD_CHUNK_SIZE):
                    if not chunk:
                        continue
                    output.write(chunk)
                    downloaded += len(chunk)
                    now = time.monotonic()
                    if now - last_report >= 5:
                        percent = min(100, int(downloaded * 100 / expected_size))
                        print(
                            f"    {name}: {percent}% "
                            f"({_format_bytes(downloaded)}/{_format_bytes(expected_size)})"
                        )
                        last_report = now

            actual_size = partial.stat().st_size
            if actual_size != expected_size:
                raise RuntimeError(
                    f"下载大小不完整 ({actual_size}/{expected_size} bytes)"
                )
            actual_sha = build._sha256_file(partial)
            if actual_sha != expected_sha:
                partial.write_bytes(b"")
                raise RuntimeError("SHA256 不一致，已清空续传片段")

            os.replace(partial, destination)
            _write_release_state(
                version,
                release_sha,
                state_phase,
                "in_progress",
                artifact=name,
                artifact_status="complete",
                downloaded_bytes=expected_size,
                expected_bytes=expected_size,
            )
            return destination
        except (build.requests.exceptions.RequestException, OSError, RuntimeError) as exc:
            last_error = f"{type(exc).__name__}: {exc}"
            current_size = partial.stat().st_size if partial.exists() else 0
            artifact_status = (
                "stalled"
                if isinstance(exc, build.requests.exceptions.ReadTimeout)
                else "interrupted"
            )
            _write_release_state(
                version,
                release_sha,
                state_phase,
                "in_progress",
                artifact=name,
                artifact_status=artifact_status,
                downloaded_bytes=current_size,
                expected_bytes=expected_size,
            )
            if attempt < attempts:
                delay = 3 * attempt
                print(
                    f"  [重试] {name} 下载中断 ({attempt}/{attempts})，"
                    f"保留 {_format_bytes(current_size)}，{delay}s 后续传..."
                )
                time.sleep(delay)
        finally:
            if response is not None:
                response.close()

    _fail(f"GitHub 附件下载失败：{name}（{last_error}）")


def _format_bytes(size: int) -> str:
    value = float(max(0, size))
    for unit in ("B", "KiB", "MiB", "GiB"):
        if value < 1024 or unit == "GiB":
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} GiB"


def _github_download_session() -> build.requests.Session:
    """Return a GitHub session that honors the configured proxy environment."""
    session = build.requests.Session()
    session.trust_env = True
    return session


def _download_verified_github_artifact(
    tag: str,
    name: str,
    github_assets: dict,
    artifact_dir: Path,
    *,
    release_sha: str,
    session=None,
    state_phase: str = "download_github_artifacts",
) -> Path:
    """Download or reuse one GitHub asset and verify its size and SHA256."""
    remote = github_assets.get(name)
    if not _asset_has_integrity(remote):
        _fail(f"GitHub Draft 缺少可校验附件：{name}")
    path = artifact_dir / name
    if path.exists():
        same, reason = build._github_asset_matches_local(tag, path, remote)
        if same:
            print(f"  [复用] 本机镜像缓存已校验: {name} ({reason})")
            _write_release_state(
                tag.removeprefix("v"),
                release_sha,
                state_phase,
                "in_progress",
                artifact=name,
                artifact_status="complete",
                downloaded_bytes=path.stat().st_size,
                expected_bytes=int(remote.get("size") or 0),
            )
            return path
        print(f"  [刷新] 本机镜像缓存不一致: {name} ({reason})")
    _download_github_asset_resumable(
        remote,
        path,
        version=tag.removeprefix("v"),
        release_sha=release_sha,
        session=session,
        state_phase=state_phase,
    )
    same, reason = build._github_asset_matches_local(tag, path, remote)
    if not same:
        _fail(f"GitHub 附件下载后校验失败：{name}（{reason}）")
    print(f"  [OK] GitHub 附件已下载并校验: {name} ({reason})")
    return path


def _download_verified_github_artifacts(
    tag: str,
    github_assets: dict,
    artifact_dir: Path,
    *,
    release_sha: str,
) -> list[Path]:
    """Download staged assets into the ignored local cache and verify SHA256."""
    artifact_dir.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    session = _github_download_session()
    try:
        for name in _release_artifacts(tag):
            paths.append(
                _download_verified_github_artifact(
                    tag,
                    name,
                    github_assets,
                    artifact_dir,
                    release_sha=release_sha,
                    session=session,
                )
            )
    finally:
        session.close()
    return paths


def _upload_github_artifacts(tag: str, artifacts: list[Path]) -> dict:
    remote_assets = build._get_github_release_assets(tag)
    for path in artifacts:
        remote = remote_assets.get(path.name)
        if remote:
            same, reason = build._github_asset_matches_local(tag, path, remote)
            if same:
                print(f"  [跳过] GitHub 已有且一致: {path.name} ({reason})")
                continue
            print(f"  [更新] GitHub {path.name}: {reason}")
        build._upload_github_release_asset(tag, path)
        if not build._ensure_github_release_asset_matches_local(tag, path):
            _fail(f"GitHub 附件上传后校验失败：{path.name}")
    verified = build._verify_github_release_assets_complete(tag)
    if verified is None:
        _fail("GitHub Release 附件完整性校验失败")
    return verified


def _canonical_downloads_cn(version: str) -> dict[str, str]:
    return build._release_downloads(version, source="gitee")


def _prune_gitee_old_assets(version: str, token: str) -> None:
    """Keep Release records and tags, but remove every older Gitee attachment."""
    if not build._gitee_clean_old_assets(
        version,
        apply=True,
        token=token,
        skip_ping=True,
    ):
        _fail("Gitee 旧版本附件清理失败")


def _gitee_artifact_is_reusable(
    version: str,
    path: Path,
    github_asset: dict,
    gitee_asset: dict | None,
) -> bool:
    """Return whether a verified local/GitHub asset matches Gitee by size."""
    if not _asset_has_integrity(github_asset):
        _fail(f"GitHub 附件缺少完整性元数据：{path.name}")
    same_github, _reason = build._github_asset_matches_local(
        f"v{version}", path, github_asset
    )
    try:
        same_size = (
            int((gitee_asset or {}).get("size") or 0)
            == int(github_asset.get("size") or 0)
            > 0
        )
    except (AttributeError, TypeError, ValueError):
        same_size = False
    return same_github and same_size


def _upload_one_gitee_artifact(
    version: str,
    title: str,
    body: str,
    path: Path,
    release_cache: dict,
) -> str:
    """Upload one verified artifact using Gitee's proxy-independent session."""
    uploaded = build._gitee_upload_artifacts(
        version,
        title,
        body,
        [path],
        release_cache=release_cache,
        large_workers=1,
        fail_fast=True,
    )
    if not uploaded:
        _fail(f"Gitee Release 附件上传失败：{path.name}")
    return path.name


def _transfer_github_artifacts_to_gitee(
    version: str,
    title: str,
    body: str,
    github_assets: dict,
    artifact_dir: Path,
    *,
    release_sha: str,
    token: str | None = None,
    release_cache: dict | None = None,
    upload_workers: int = DEFAULT_GITEE_UPLOAD_WORKERS,
) -> dict[str, str]:
    """Pipeline verified GitHub downloads into bounded direct Gitee uploads."""
    try:
        upload_workers = int(upload_workers)
    except (TypeError, ValueError):
        _fail("Gitee 上传并发数必须是 1、2 或 3")
    if upload_workers not in {1, 2, 3}:
        _fail("Gitee 上传并发数必须是 1、2 或 3")

    cache = release_cache or build._gitee_get_release_cache(
        version,
        title,
        body,
        token=token,
        skip_ping=bool(token),
    )
    if cache is None:
        _fail("无法创建或读取 Gitee Release")

    artifact_dir.mkdir(parents=True, exist_ok=True)
    tag = f"v{version}"
    existing = cache.get("existing", {})
    futures: dict[Future, Path] = {}
    completed: set[Future] = set()
    session = _github_download_session()

    def record_upload(future: Future) -> None:
        path = futures[future]
        if future in completed:
            return
        try:
            future.result()
        except BaseException:
            _write_release_state(
                version,
                release_sha,
                "artifact_transfer",
                "in_progress",
                artifact=path.name,
                gitee_status="failed",
            )
            raise
        completed.add(future)
        _write_release_state(
            version,
            release_sha,
            "artifact_transfer",
            "in_progress",
            artifact=path.name,
            gitee_status="complete",
        )

    print(
        "  [流水线] GitHub 按环境代理下载；"
        f"Gitee 直连上传 workers={upload_workers}"
    )
    try:
        with ThreadPoolExecutor(max_workers=upload_workers) as executor:
            for name in _release_artifacts(version):
                path = _download_verified_github_artifact(
                    tag,
                    name,
                    github_assets,
                    artifact_dir,
                    release_sha=release_sha,
                    session=session,
                    state_phase="artifact_transfer",
                )
                if _gitee_artifact_is_reusable(
                    version,
                    path,
                    github_assets[name],
                    existing.get(name),
                ):
                    print(f"  [复用] Gitee 已有同尺寸已验证产物: {name}")
                    _write_release_state(
                        version,
                        release_sha,
                        "artifact_transfer",
                        "in_progress",
                        artifact=name,
                        gitee_status="complete",
                    )
                else:
                    future = executor.submit(
                        _upload_one_gitee_artifact,
                        version,
                        title,
                        body,
                        path,
                        cache,
                    )
                    futures[future] = path
                    _write_release_state(
                        version,
                        release_sha,
                        "artifact_transfer",
                        "in_progress",
                        artifact=name,
                        gitee_status="uploading",
                    )

                for ready in tuple(futures):
                    if ready.done() and ready not in completed:
                        record_upload(ready)

            for future in as_completed(futures):
                record_upload(future)
    finally:
        session.close()

    if not build._verify_gitee_release_assets_complete(tag, github_assets, cache):
        _fail("Gitee Release 附件完整性校验失败")
    return _canonical_downloads_cn(version)


def _publish_gitee_artifacts(
    version: str,
    title: str,
    body: str,
    artifacts: list[Path],
    github_assets: dict,
    release_cache: dict | None = None,
    token: str | None = None,
    upload_workers: int = DEFAULT_GITEE_UPLOAD_WORKERS,
) -> dict[str, str]:
    cache = release_cache or build._gitee_get_release_cache(
        version,
        title,
        body,
        token=token,
        skip_ping=bool(token),
    )
    if cache is None:
        _fail("无法创建或读取 Gitee Release")

    # Gitee does not expose a SHA256 digest.  For an interrupted same-version
    # resume, a size match against the already SHA-verified GitHub/local asset
    # is the maintained release contract and avoids downloading all large
    # Gitee files merely to decide that no upload is needed.
    pending: list[Path] = []
    existing = cache.get("existing", {})
    for path in artifacts:
        github_asset = github_assets.get(path.name)
        if _gitee_artifact_is_reusable(
            version, path, github_asset, existing.get(path.name)
        ):
            print(f"  [复用] Gitee 已有同尺寸已验证产物: {path.name}")
            continue
        pending.append(path)

    if pending:
        uploaded = build._gitee_upload_artifacts(
            version,
            title,
            body,
            pending,
            release_cache=cache,
            large_workers=upload_workers,
            fail_fast=True,
        )
        if not uploaded:
            _fail("Gitee Release 附件上传失败")
    else:
        print("  [跳过] Gitee 本版本全部产物均已完整")
    if not build._verify_gitee_release_assets_complete(
        f"v{version}", github_assets, cache
    ):
        _fail("Gitee Release 附件完整性校验失败")
    return _canonical_downloads_cn(version)


def _publish_github_release(tag: str) -> None:
    info = build._get_github_release_info(tag)
    if info is None:
        _fail("GitHub Release 不存在")
    if info.get("isDraft"):
        _run_external(
            ["gh", "release", "edit", tag, "--draft=false"],
            f"发布 GitHub Release {tag}",
            postcondition=lambda: (
                (current := build._get_github_release_info(tag)) is not None
                and not current.get("isDraft")
            ),
        )
        print(f"  [OK] GitHub Release 已正式发布: {tag}")
    else:
        print(f"  [跳过] GitHub Release 已是正式版本: {tag}")


def _fetch_and_assert_current_master_compatible(release_sha: str) -> str:
    """Re-read origin/master and reject an unsafe release/resume race."""
    _run_external(
        ["git", "fetch", "origin", "master"],
        "拉取最新 GitHub master",
    )
    master_sha = build._remote_ref_commit("origin", "refs/heads/master")
    if not master_sha:
        _fail("无法读取最新 origin/master")
    _assert_resume_head_compatible(release_sha, master_sha)
    return master_sha


def _switch_to_current_master(release_sha: str) -> str:
    master_sha = _fetch_and_assert_current_master_compatible(release_sha)
    _run(["git", "switch", "-C", "master", "origin/master"])
    return master_sha


def _commit_and_sync_manifest(
    version: str,
    body: str,
    downloads_cn: dict[str, str],
    github_assets: dict,
    release_sha: str,
    gitee_token: str,
) -> str:
    _switch_to_current_master(release_sha)
    metadata = build._release_asset_metadata_from_remote_assets(github_assets.values())
    build._assert_update_asset_metadata_complete(metadata, version=version)
    changed = build.update_latest_json(
        version,
        body,
        downloads_cn,
        asset_metadata=metadata,
        require_complete_assets=True,
    )
    if changed:
        changed_paths = _working_tree_paths()
        if changed_paths != {"latest.json"}:
            _fail(
                "更新自动更新清单时出现非预期文件："
                + ", ".join(sorted(changed_paths))
            )
        _run(["git", "add", "latest.json"])
        _run(["git", "commit", "-m", "chore: 更新自动更新清单"])
        current_master = _git_text("rev-parse", "HEAD")
        _run_external(
            ["git", "push", "origin", "master"],
            "推送 latest.json 到 GitHub master",
            postcondition=lambda: build._remote_ref_commit(
                "origin", "refs/heads/master"
            ) == current_master,
        )
        print("  [OK] latest.json 已提交并推送到 GitHub master")
    else:
        print("  [跳过] latest.json 已一致")

    current_master = _git_text("rev-parse", "HEAD")
    _sanitized_git_push(
        _gitee_authenticated_url(gitee_token),
        "HEAD:refs/heads/master",
        gitee_token,
        remote_ref="refs/heads/master",
        expected_commit=current_master,
    )
    if build._remote_ref_commit("gitee", "refs/heads/master") != current_master:
        _fail("Gitee master 推送后校验失败")
    print("  [OK] Gitee master 已同步")
    return current_master


def _public_asset_probe(
    url: str,
    expected_name: str,
    expected_size: int,
) -> tuple[bool, str]:
    """Validate one public file without downloading the complete artifact."""
    headers = {"Range": "bytes=0-511"}
    response = None
    try:
        response = build._request_get(
            url,
            headers=headers,
            allow_redirects=True,
            stream=True,
            timeout=30,
        )
        if response.status_code not in {200, 206}:
            return False, f"HTTP {response.status_code}"
        final_url = str(response.url or url)
        if not final_url.lower().startswith("https://"):
            return False, "final download URL is not HTTPS"
        content_type = str(response.headers.get("Content-Type") or "").lower()
        if "text/html" in content_type or "application/json" in content_type:
            return False, f"unexpected Content-Type {content_type!r}"
        disposition = str(response.headers.get("Content-Disposition") or "")
        requested_name = url.rstrip("/").rsplit("/", 1)[-1]
        if expected_name not in disposition and requested_name != expected_name:
            return False, "filename mismatch"

        actual_size = 0
        content_range = str(response.headers.get("Content-Range") or "")
        range_match = re.search(r"/(\d+)$", content_range)
        if range_match:
            actual_size = int(range_match.group(1))
        else:
            try:
                actual_size = int(response.headers.get("Content-Length") or 0)
            except (TypeError, ValueError):
                actual_size = 0
        if actual_size != expected_size:
            return False, f"size {actual_size}, expected {expected_size}"

        prefix = b""
        for chunk in response.iter_content(chunk_size=512):
            if chunk:
                prefix = chunk[:512]
                break
        if expected_name.endswith(".exe") and not prefix.startswith(b"MZ"):
            return False, "missing PE MZ header"
        if expected_name.endswith(".zip") and not prefix.startswith(b"PK"):
            return False, "missing ZIP PK header"
        if not prefix:
            return False, "empty response body"
        return True, final_url
    except build.requests.exceptions.RequestException as exc:
        return False, type(exc).__name__
    finally:
        if response is not None:
            response.close()


def verify_public_endpoints(version: str, attempts: int = 6, delay: int = 10) -> dict:
    """Verify public downloads plus both remotely served manifests."""
    version = _normalize_version(version)
    latest = json.loads((BASE_DIR / "latest.json").read_text(encoding="utf-8"))
    asset_names = dict(build._release_asset_items(version))
    probes: list[tuple[str, str, int]] = []
    for source in ("downloads", "downloads_cn"):
        downloads = latest.get(source) or {}
        for key, name in asset_names.items():
            try:
                size = int(((latest.get("assets") or {}).get(key) or {}).get("size") or 0)
            except (TypeError, ValueError):
                size = 0
            url = str(downloads.get(key) or "")
            if url and size > 0:
                probes.append((url, name, size))
    expected_probe_count = len(asset_names) * 2
    if len(probes) != expected_probe_count:
        _fail(
            "latest.json 双源公开下载地址不完整："
            f"预期 {expected_probe_count} 个，实际 {len(probes)} 个"
        )

    manifest_urls = (
        f"https://raw.githubusercontent.com/{GITEE_OWNER}/{GITEE_REPO}/master/latest.json",
        f"https://gitee.com/{GITEE_OWNER}/{GITEE_REPO}/raw/master/latest.json",
    )
    last_errors: list[str] = []
    for attempt in range(1, attempts + 1):
        last_errors = []
        for url, name, size in probes:
            ok, detail = _public_asset_probe(url, name, size)
            if not ok:
                last_errors.append(f"{url} -> {detail}")
        for url in manifest_urls:
            try:
                response = build._request_get(url, timeout=30)
                response.raise_for_status()
                remote = response.json()
                if remote != latest:
                    last_errors.append(f"{url} -> 完整内容不一致")
            except (build.requests.exceptions.RequestException, ValueError) as exc:
                last_errors.append(f"{url} -> {type(exc).__name__}")
        if not last_errors:
            print(
                f"  [OK] {expected_probe_count} 个公开下载地址和"
                "双远端在线清单均可用"
            )
            return {
                "downloads_checked": len(probes),
                "manifests_checked": len(manifest_urls),
            }
        if attempt < attempts:
            print(f"  [等待] 公开资源尚未全部生效（{attempt}/{attempts}）")
            time.sleep(delay)
    _fail("公开资源验收失败：" + "; ".join(last_errors))


def stage_github_release(
    version: str,
    authorization: str,
    release_sha: str,
    approved_content_sha: str,
) -> None:
    """Stage the immutable tag and complete GitHub Draft on hosted Actions."""
    version = _normalize_version(version)
    validate_authorization(version, authorization)
    if os.environ.get("GITHUB_ACTIONS") != "true":
        _fail("GitHub 暂存阶段只能在 GitHub Actions 中运行")
    if os.environ.get("GITHUB_EVENT_NAME") != "workflow_dispatch":
        _fail("GitHub 暂存阶段只能由 workflow_dispatch 手动触发")
    if os.environ.get("GITHUB_REF_NAME") != "master":
        _fail("GitHub 暂存阶段只能从 master 触发")
    _require_github_publish_secret()
    release_sha = release_sha.strip()
    head_sha = _git_text("rev-parse", "HEAD")
    if head_sha != release_sha:
        _fail("stage-github job 未检出 prepare 阶段确定的发布提交")
    if _version_at_ref(release_sha) != version:
        _fail("发布提交版本与请求版本不一致")

    # Build jobs can run for tens of minutes. Recheck master immediately before
    # the first remote mutation, but leave all Gitee/public mutations to local.
    _fetch_and_assert_current_master_compatible(release_sha)
    review = release_content_review.review_release_content(version, release_sha)
    release_content_review.require_approved_content(review, approved_content_sha)
    tag = f"v{version}"
    title = review["release_title"]
    body = review["release_body"]
    tag_notes = _notes_file(title, body)
    try:
        _ensure_origin_tag(tag, release_sha, tag_notes)
    finally:
        tag_notes.unlink(missing_ok=True)
    _ensure_github_release(tag, title, body)
    artifacts = _ensure_local_artifacts(tag)
    _upload_github_artifacts(tag, artifacts)
    print(
        f"\n[OK] {tag} GitHub Draft 和 {len(artifacts)} 个附件已暂存，"
        "等待本机校验并公开主源后镜像 Gitee"
    )


def finalize_release_local(
    version: str,
    authorization: str,
    release_sha: str,
    approved_content_sha: str,
) -> dict:
    """Mirror staged artifacts from local machine, publish, and verify."""
    version = _normalize_version(version)
    validate_authorization(version, authorization)
    release_sha = release_sha.strip()
    phase = "local_preflight"
    _report_previous_release_state(version, release_sha)
    _write_release_state(version, release_sha, phase, "in_progress")
    try:
        gitee_token = require_local_gitee_access()
        _ensure_gitee_remote()

        if _git_text("branch", "--show-current") != "master":
            _fail("本机最终发布只能从 master 执行")
        if _working_tree_paths():
            _fail("本机最终发布前工作区必须干净")
        head_sha = _git_text("rev-parse", "HEAD")
        _assert_resume_head_compatible(release_sha, head_sha)
        if _version_at_ref(release_sha) != version:
            _fail("发布提交版本与请求版本不一致")
        _fetch_and_assert_current_master_compatible(release_sha)

        review = release_content_review.review_release_content(version, release_sha)
        release_content_review.require_approved_content(review, approved_content_sha)
        _write_release_state(
            version, release_sha, phase, "complete",
        )

        phase = "github_staged"
        _write_release_state(version, release_sha, phase, "in_progress")
        tag = f"v{version}"
        if build._remote_tag_commit("origin", tag) != release_sha:
            _fail("GitHub 暂存未完成：远端 tag 缺失或指向不一致")
        title = review["release_title"]
        body = review["release_body"]
        info = build._get_github_release_info(tag)
        if info is None:
            _fail("GitHub 暂存未完成：Draft Release 不存在")
        github_assets = build._verify_github_release_assets_complete(tag)
        if github_assets is None:
            _fail("GitHub 暂存未完成：本版本附件不完整")
        _write_release_state(version, release_sha, phase, "complete")

        # The hosted stage already verifies local build bytes against GitHub's
        # size/SHA256 digest. Publish the primary source before downloading the
        # same bytes again for the local Gitee mirror.
        phase = "github_public"
        _write_release_state(version, release_sha, phase, "in_progress")
        _fetch_and_assert_current_master_compatible(release_sha)
        _publish_github_release(tag)
        _write_release_state(version, release_sha, phase, "complete")

        phase = "gitee_tag"
        _write_release_state(version, release_sha, phase, "in_progress")
        _ensure_gitee_tag(tag, release_sha, gitee_token)
        _write_release_state(version, release_sha, phase, "complete")

        phase = "gitee_prune_old_assets"
        if _completed_phase_details(version, release_sha, phase):
            print("  [续跑] Gitee 历史附件已经清理，跳过重复扫描和删除")
        else:
            _write_release_state(version, release_sha, phase, "in_progress")
            _prune_gitee_old_assets(version, gitee_token)
            _write_release_state(
                version,
                release_sha,
                phase,
                "complete",
                details={"keep_tag": tag},
            )

        phase = "artifact_transfer"
        _write_release_state(version, release_sha, phase, "in_progress")
        mirror_dir = build.DIST_DIR / "release-mirror" / tag
        downloads_cn = _transfer_github_artifacts_to_gitee(
            version,
            title,
            body,
            github_assets,
            mirror_dir,
            release_sha=release_sha,
            token=gitee_token,
            upload_workers=DEFAULT_GITEE_UPLOAD_WORKERS,
        )
        _write_release_state(version, release_sha, phase, "complete")

        phase = "manifest_sync"
        _write_release_state(version, release_sha, phase, "in_progress")
        manifest_sha = _commit_and_sync_manifest(
            version,
            body,
            downloads_cn,
            github_assets,
            release_sha,
            gitee_token,
        )
        _write_release_state(
            version,
            release_sha,
            phase,
            "complete",
            details={"manifest_sha": manifest_sha},
        )

        phase = "public_verification"
        previous_verification = _completed_phase_details(
            version, release_sha, phase,
        )
        if previous_verification.get("manifest_sha") == manifest_sha:
            print("  [续跑] 当前清单提交已经完成公开验收，跳过重复整套核验")
            verification = previous_verification
        else:
            _write_release_state(version, release_sha, phase, "in_progress")
            if not build._verify_release_remote_state(version):
                _fail("双远端发布状态核验失败")
            endpoint_result = verify_public_endpoints(version)
            verification = {
                "manifest_sha": manifest_sha,
                "verified_at": datetime.now().isoformat(timespec="seconds"),
                **endpoint_result,
            }
            _write_release_state(
                version,
                release_sha,
                phase,
                "complete",
                details=verification,
            )
        _print_release_timing_summary(version, release_sha)
        print(f"\n[OK] {tag} 正式发布、自动更新清单和线上验收全部完成")
        return {
            "version": version,
            "release_sha": release_sha,
            **verification,
        }
    except BaseException as exc:
        secrets = (locals().get("gitee_token") or "",)
        safe_error = release_retry.redact_sensitive_text(exc, secrets)
        _write_release_state(
            version,
            release_sha,
            phase,
            "failed",
            error_type=type(exc).__name__,
            error_message=safe_error,
        )
        print(
            f"\n[失败] 正式发布停在 {phase}，"
            f"详见 logs/release-v{version}.log"
        )
        raise


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="正式发布暂存与本机最终发布编排")
    subparsers = parser.add_subparsers(dest="command", required=True)

    prepare = subparsers.add_parser("prepare", help="运行无副作用严格门禁")
    prepare.add_argument("--version", required=True)
    prepare.add_argument("--authorization", required=True)
    prepare.add_argument("--approved-content-sha", default="")
    prepare.add_argument("--github-output")
    prepare.add_argument("--dry-run", action="store_true")

    stage = subparsers.add_parser("stage-github", help="在 Actions 暂存 GitHub Draft")
    stage.add_argument("--version", required=True)
    stage.add_argument("--authorization", required=True)
    stage.add_argument("--release-sha", required=True)
    stage.add_argument("--approved-content-sha", required=True)

    finalize = subparsers.add_parser("finalize-local", help="从本机镜像 Gitee 并公开发布")
    finalize.add_argument("--version", required=True)
    finalize.add_argument("--authorization", required=True)
    finalize.add_argument("--release-sha", required=True)
    finalize.add_argument("--approved-content-sha", required=True)

    verify = subparsers.add_parser("verify-public", help="核验公开下载和在线清单")
    verify.add_argument("--version", required=True)

    return parser


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()
    try:
        if args.command == "prepare":
            env_dry_run = os.environ.get("RELEASE_DRY_RUN", "").lower() == "true"
            prepare_release(
                args.version,
                args.authorization,
                approved_content_sha=args.approved_content_sha,
                dry_run=args.dry_run or env_dry_run,
                github_output=args.github_output,
            )
        elif args.command == "stage-github":
            stage_github_release(
                args.version, args.authorization, args.release_sha,
                args.approved_content_sha,
            )
        elif args.command == "finalize-local":
            finalize_release_local(
                args.version, args.authorization, args.release_sha,
                args.approved_content_sha,
            )
        else:
            verify_public_endpoints(args.version)
    except (ReleaseAutomationError, release_content_review.ReleaseContentReviewError) as exc:
        print(f"[错误] {exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
