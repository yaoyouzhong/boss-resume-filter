"""Local driver for the repository's single formal-release control plane.

The default mode runs a local, mutation-free release preview.  Execution
requires ``--execute`` plus the exact authorization text ``确认正式发布 vX.Y``.
The driver dispatches the GitHub staging workflow, binds to the exact run it
created (or safely reuses an active matching run), waits for completion, then
downloads the staged artifacts and mirrors them to Gitee from this machine.
Only after both stores are complete does it publish GitHub, synchronize
``latest.json`` and run the public release verifier.

``scripts/release_ci.py`` remains the single deterministic mutation contract;
this driver decides whether its hosted staging or local finalization phase is
needed for an idempotent resume.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any


BASE_DIR = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = Path(__file__).resolve().parent
for import_path in (BASE_DIR, SCRIPTS_DIR):
    if str(import_path) not in sys.path:
        sys.path.insert(0, str(import_path))

import build  # noqa: E402
import release_ci  # noqa: E402
import release_content_review  # noqa: E402
import release_prepare  # noqa: E402


DEFAULT_RUN_DISCOVERY_TIMEOUT = 90
DEFAULT_RELEASE_TIMEOUT = 3 * 60 * 60
DEFAULT_POLL_INTERVAL = 15
ACTIVE_RUN_STATES = {"queued", "in_progress", "pending", "requested", "waiting"}


class ReleaseDispatchError(RuntimeError):
    """The local formal-release driver could not satisfy its contract."""


def _fail(message: str) -> None:
    raise ReleaseDispatchError(message)


def _run(
    args: list[str],
    *,
    check: bool = True,
    capture_output: bool = False,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        cwd=BASE_DIR,
        check=check,
        capture_output=capture_output,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def _git_text(*args: str) -> str:
    result = _run(["git", *args], capture_output=True)
    return result.stdout.strip()


def expected_authorization(version: str) -> str:
    return f"确认正式发布 v{version}"


def validate_authorization(version: str, authorization: str) -> None:
    expected = expected_authorization(version)
    if authorization != expected:
        _fail(f"正式发布授权不匹配：必须准确填写 {expected!r}")


def _load_json(result: subprocess.CompletedProcess[str]) -> Any:
    try:
        return json.loads(result.stdout or "null")
    except json.JSONDecodeError as exc:
        _fail(f"命令返回了无效 JSON：{exc}")


def _list_release_runs() -> list[dict[str, Any]]:
    result = _run(
        [
            "gh", "run", "list",
            "--workflow", "release.yml",
            "--event", "workflow_dispatch",
            "--limit", "30",
            "--json",
            "databaseId,displayTitle,headSha,status,conclusion,url,createdAt",
        ],
        capture_output=True,
    )
    data = _load_json(result)
    if not isinstance(data, list):
        _fail("无法读取 GitHub 暂存工作流运行列表")
    return data


def _matching_runs(
    runs: list[dict[str, Any]],
    version: str,
    release_sha: str,
) -> list[dict[str, Any]]:
    title = f"Release v{version}"
    return [
        run for run in runs
        if str(run.get("displayTitle") or "") == title
        and run.get("headSha") == release_sha
    ]


def _active_run(runs: list[dict[str, Any]]) -> dict[str, Any] | None:
    return next(
        (
            run for run in runs
            if str(run.get("status") or "").lower() in ACTIVE_RUN_STATES
        ),
        None,
    )


def _working_tree_clean() -> bool:
    return not bool(_git_text("status", "--porcelain"))


def preflight(version: str, *, approved_content_sha: str = "") -> dict[str, Any]:
    """Run the local strict gate and return the immutable release plan."""
    version = release_prepare.normalize_version(version)
    if _git_text("branch", "--show-current") != "master":
        _fail("正式发布只能从本地 master 触发")
    if not _working_tree_clean():
        _fail("正式发布前工作区必须干净")

    _run(["gh", "auth", "status", "--hostname", "github.com"])
    _run(["git", "fetch", "origin"])
    _run(["git", "fetch", "gitee"])
    head_sha = _git_text("rev-parse", "HEAD")
    origin_master = _git_text("rev-parse", "origin/master")
    gitee_master = _git_text("rev-parse", "gitee/master")
    if origin_master != gitee_master:
        _fail("GitHub/Gitee master 不一致，拒绝正式发布")
    if head_sha != origin_master:
        _fail("本地 master 不是最新 origin/master")

    try:
        gate = release_ci.prepare_release(
            version,
            expected_authorization(version),
            approved_content_sha=approved_content_sha,
            dry_run=True,
            reuse_reviewed_gate=bool(approved_content_sha),
        )
    except (
        release_ci.ReleaseAutomationError,
        release_content_review.ReleaseContentReviewError,
        SystemExit,
    ) as exc:
        _fail(f"正式发布严格门禁未通过：{exc}")

    runs = _matching_runs(_list_release_runs(), version, gate["release_sha"])
    staged = (
        gate["resume"] == "true"
        and gate["needs_windows"] == "false"
        and gate["needs_macos"] == "false"
    )
    published = False
    if staged:
        published = bool(build._verify_release_remote_state(version))
    return {
        "version": version,
        "release_sha": gate["release_sha"],
        "tag": gate["tag"],
        "resume": gate["resume"],
        "needs_windows": gate["needs_windows"],
        "needs_macos": gate["needs_macos"],
        "release_title": gate["release_title"],
        "release_body": gate["release_body"],
        "content_sha": gate["content_sha"],
        "staged": staged,
        "published": published,
        "runs": runs,
    }


def _print_plan(plan: dict[str, Any]) -> None:
    print(f"\n>>> {plan['tag']} 正式发布预览")
    print(f"  发布提交: {plan['release_sha']}")
    print(f"  模式: {'断点续跑' if plan['resume'] == 'true' else '首次发布'}")
    print(f"  Windows 构建: {'需要' if plan['needs_windows'] == 'true' else '复用'}")
    print(f"  macOS 构建: {'需要' if plan['needs_macos'] == 'true' else '复用'}")
    print(f"  GitHub 暂存: {'已完成' if plan['staged'] else '待执行'}")
    print("\n>>> 发布内容审核（必须人工确认）")
    print(f"  标题: {plan['release_title']}")
    print(plan["release_body"])
    print(f"\n  内部凭证: {plan['content_sha'][:12]}")
    print(f"  RELEASE_CONTENT_SHA={plan['content_sha']}")
    if plan["published"]:
        print("  公开状态: 已完整发布")
    else:
        print("  Gitee 镜像: 将由本机串行完成")
    active = _active_run(plan["runs"])
    if active:
        print(f"  Actions: 已有运行中的任务 {active.get('url', '')}")


def _dispatch_workflow(
    version: str,
    authorization: str,
    approved_content_sha: str,
) -> None:
    _run([
        "gh", "workflow", "run", "release.yml",
        "--ref", "master",
        "-f", f"version={version}",
        "-f", f"authorization={authorization}",
        "-f", f"content_sha={approved_content_sha}",
        "-f", "dry_run=false",
    ])


def _discover_new_run(
    version: str,
    release_sha: str,
    previous_ids: set[int],
    *,
    timeout: int = DEFAULT_RUN_DISCOVERY_TIMEOUT,
    poll_interval: int = 3,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while True:
        matching = _matching_runs(_list_release_runs(), version, release_sha)
        new_runs = [
            run for run in matching
            if int(run.get("databaseId") or 0) not in previous_ids
        ]
        if new_runs:
            return new_runs[0]
        if time.monotonic() >= deadline:
            _fail("GitHub 已接受触发请求，但未能定位对应的暂存工作流 run")
        time.sleep(poll_interval)


def _run_view(run_id: int) -> dict[str, Any]:
    result = _run(
        [
            "gh", "run", "view", str(run_id),
            "--json", "databaseId,displayTitle,headSha,status,conclusion,url,jobs",
        ],
        capture_output=True,
    )
    data = _load_json(result)
    if not isinstance(data, dict):
        _fail(f"无法读取 Actions run #{run_id}")
    return data


def _progress_signature(run: dict[str, Any]) -> tuple[Any, ...]:
    jobs = tuple(
        (
            job.get("name"),
            str(job.get("status") or "").lower(),
            str(job.get("conclusion") or "").lower(),
        )
        for job in (run.get("jobs") or [])
    )
    return (
        str(run.get("status") or "").lower(),
        str(run.get("conclusion") or "").lower(),
        jobs,
    )


def _print_progress(run: dict[str, Any]) -> None:
    print(
        f"  Actions: {run.get('status') or 'unknown'}"
        + (f" / {run.get('conclusion')}" if run.get("conclusion") else "")
    )
    for job in run.get("jobs") or []:
        status = job.get("conclusion") or job.get("status") or "unknown"
        print(f"    - {job.get('name', 'job')}: {status}")


def wait_for_run(
    run_id: int,
    *,
    timeout: int = DEFAULT_RELEASE_TIMEOUT,
    poll_interval: int = DEFAULT_POLL_INTERVAL,
) -> dict[str, Any]:
    """Wait for one exact workflow run and stop on any non-success result."""
    deadline = time.monotonic() + timeout
    previous_signature: tuple[Any, ...] | None = None
    while True:
        run = _run_view(run_id)
        signature = _progress_signature(run)
        if signature != previous_signature:
            _print_progress(run)
            previous_signature = signature
        status = str(run.get("status") or "").lower()
        if status == "completed":
            if str(run.get("conclusion") or "").lower() != "success":
                _fail(
                    f"GitHub 暂存工作流失败：{run.get('conclusion') or 'unknown'}；"
                    f"可修复后用同一版本安全续跑：{run.get('url', '')}"
                )
            return run
        if time.monotonic() >= deadline:
            _fail(f"等待 GitHub 暂存超时，工作流仍在运行：{run.get('url', '')}")
        time.sleep(poll_interval)


def _synchronize_local_master() -> str:
    """Fast-forward local master only after both remote masters agree."""
    if _git_text("branch", "--show-current") != "master" or not _working_tree_clean():
        _fail("正式发布已完成，但本地 master 无法安全自动同步")
    _run(["git", "fetch", "origin"])
    _run(["git", "fetch", "gitee"])
    local_sha = _git_text("rev-parse", "HEAD")
    origin_master = _git_text("rev-parse", "origin/master")
    gitee_master = _git_text("rev-parse", "gitee/master")
    if origin_master != gitee_master:
        _fail("正式发布已完成，但 GitHub/Gitee master 尚未一致")
    if local_sha != origin_master:
        if not release_prepare._is_ancestor(local_sha, origin_master):
            _fail("正式发布已完成，但本地 master 与远端发生分叉")
        _run(["git", "merge", "--ff-only", "origin/master"])
    return origin_master


def _project_python() -> str:
    bundled = BASE_DIR / "pack_venv" / "Scripts" / "python.exe"
    return str(bundled) if bundled.is_file() else sys.executable


def _verify_public_release(version: str) -> None:
    _run([_project_python(), "build.py", "--verify-release", version])


def _finish_success(
    version: str,
    run: dict[str, Any] | None,
    *,
    already_published: bool = False,
) -> dict[str, Any]:
    master_sha = _synchronize_local_master()
    _verify_public_release(version)
    print(f"\n[OK] v{version} 正式发布已完成并通过公开验收")
    print(f"  master: {master_sha}")
    if run:
        print(f"  Actions: {run.get('url', '')}")
    return {
        "mode": "already_published" if already_published else "published",
        "version": version,
        "master_sha": master_sha,
        "run": run,
    }


def dispatch_release(
    version: str,
    *,
    execute: bool = False,
    authorization: str = "",
    approved_content_sha: str = "",
    timeout: int = DEFAULT_RELEASE_TIMEOUT,
    poll_interval: int = DEFAULT_POLL_INTERVAL,
) -> dict[str, Any]:
    """Preview, dispatch, monitor, and verify one formal release."""
    version = release_prepare.normalize_version(version)
    if execute:
        validate_authorization(version, authorization)
    plan = preflight(
        version,
        approved_content_sha=approved_content_sha if execute else "",
    )
    _print_plan(plan)
    if not execute:
        print("\n未触发 GitHub Actions，也未创建标签或 Release。")
        print(f"精确授权：{expected_authorization(version)}")
        return {"mode": "preview", "plan": plan}

    try:
        release_content_review.require_approved_content(plan, approved_content_sha)
    except release_content_review.ReleaseContentReviewError as exc:
        _fail(str(exc))

    if plan["published"]:
        return _finish_success(version, None, already_published=True)

    # Fail before spending hosted build minutes when the local mirror cannot
    # possibly complete. This check is read-only and never prints the token.
    release_ci.require_local_gitee_access()

    run = _active_run(plan["runs"])
    if run:
        print(f"\n[续跑] 复用已在运行的 GitHub 暂存任务：{run.get('url', '')}")
    elif not plan["staged"]:
        previous_ids = {
            int(item.get("databaseId") or 0)
            for item in _list_release_runs()
        }
        print("\n>>> 触发 Build & Stage GitHub Release")
        _dispatch_workflow(version, authorization, approved_content_sha)
        run = _discover_new_run(version, plan["release_sha"], previous_ids)
        print(f"  [OK] 已定位 Actions run：{run.get('url', '')}")
    else:
        print("\n[续跑] GitHub Draft 与三个附件已完整，跳过 Actions")

    completed = None
    if run:
        completed = wait_for_run(
            int(run["databaseId"]),
            timeout=timeout,
            poll_interval=poll_interval,
        )

    print("\n>>> 本机镜像 Gitee 并完成正式发布")
    release_ci.finalize_release_local(
        version, authorization, plan["release_sha"], approved_content_sha,
    )
    return _finish_success(version, completed)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="正式发布本地触发、监控与验收")
    parser.add_argument("--version", required=True, help="正式发布版本，不带 v 前缀")
    parser.add_argument("--execute", action="store_true", help="触发正式发布工作流")
    parser.add_argument("--authorization", default="", help="精确授权文本")
    parser.add_argument(
        "--approved-content-sha", default="",
        help="由预览步骤生成的内部发布内容凭证",
    )
    parser.add_argument("--timeout", type=int, default=DEFAULT_RELEASE_TIMEOUT)
    parser.add_argument("--poll-interval", type=int, default=DEFAULT_POLL_INTERVAL)
    return parser


def main() -> int:
    args = _build_parser().parse_args()
    try:
        dispatch_release(
            args.version,
            execute=args.execute,
            authorization=args.authorization,
            approved_content_sha=args.approved_content_sha,
            timeout=args.timeout,
            poll_interval=args.poll_interval,
        )
    except (
        ReleaseDispatchError,
        release_ci.ReleaseAutomationError,
        subprocess.CalledProcessError,
    ) as exc:
        print(f"[错误] {exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
