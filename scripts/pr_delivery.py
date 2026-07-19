"""Deterministic, single-authorization delivery for ordinary pull requests.

The default mode is mutation-free: it validates the branch, runs the local
gate, and prints the actions that an authorized execution would perform.
External writes require both ``--execute`` and the exact authorization text
``一键交付分支 <branch>``.

The workflow deliberately stops instead of resolving divergence, conflicts,
failed tests, failed CI, or dirty worktrees.  It never rebases, force-pushes,
deletes worktrees, or starts the formal release workflow.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any


BASE_DIR = Path(__file__).resolve().parents[1]
DEFAULT_CHECK_TIMEOUT = 30 * 60
DEFAULT_POLL_INTERVAL = 10
SUCCESS_CONCLUSIONS = {"SUCCESS", "NEUTRAL", "SKIPPED"}
FAILURE_CONCLUSIONS = {
    "ACTION_REQUIRED",
    "CANCELLED",
    "FAILURE",
    "STALE",
    "STARTUP_FAILURE",
    "TIMED_OUT",
}


class PRDeliveryError(RuntimeError):
    """A deterministic PR delivery contract was not satisfied."""


def _fail(message: str) -> None:
    raise PRDeliveryError(message)


def _run(
    args: list[str],
    *,
    cwd: Path | None = None,
    check: bool = True,
    capture_output: bool = False,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        cwd=cwd or BASE_DIR,
        check=check,
        capture_output=capture_output,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def _git_text(*args: str, cwd: Path | None = None) -> str:
    result = _run(["git", *args], cwd=cwd, capture_output=True)
    return result.stdout.strip()


def expected_authorization(branch: str) -> str:
    return f"一键交付分支 {branch}"


def validate_authorization(branch: str, authorization: str) -> None:
    expected = expected_authorization(branch)
    if authorization != expected:
        _fail(f"交付授权不匹配：必须准确填写 {expected!r}")


def validate_branch_name(branch: str) -> str:
    branch = str(branch or "").strip()
    if not re.fullmatch(r"codex/[A-Za-z0-9._/-]+", branch):
        _fail("只允许交付 codex/<task> 普通开发分支")
    if branch.endswith("/") or "//" in branch or ".." in branch:
        _fail("分支名称无效")
    return branch


def _current_branch() -> str:
    return _git_text("branch", "--show-current")


def _is_ancestor(ancestor: str, descendant: str) -> bool:
    result = _run(
        ["git", "merge-base", "--is-ancestor", ancestor, descendant],
        check=False,
        capture_output=True,
    )
    return result.returncode == 0


def _remote_ref(remote: str, ref: str) -> str:
    output = _git_text("ls-remote", remote, ref)
    if not output:
        return ""
    return output.split()[0]


def _remote_branch_exists(remote: str, branch: str) -> bool:
    return bool(_remote_ref(remote, f"refs/heads/{branch}"))


def _local_branch_exists(branch: str) -> bool:
    result = _run(
        ["git", "show-ref", "--verify", "--quiet", f"refs/heads/{branch}"],
        check=False,
    )
    return result.returncode == 0


def _worktree_for_branch(branch: str) -> Path | None:
    output = _git_text("worktree", "list", "--porcelain")
    target_ref = f"refs/heads/{branch}"
    for block in re.split(r"\r?\n\r?\n", output):
        path: Path | None = None
        block_branch = ""
        for line in block.splitlines():
            if line.startswith("worktree "):
                path = Path(line.removeprefix("worktree "))
            elif line.startswith("branch "):
                block_branch = line.removeprefix("branch ")
        if path is not None and block_branch == target_ref:
            return path
    return None


def _assert_clean_worktree(path: Path, label: str) -> None:
    status = _git_text("status", "--porcelain", cwd=path)
    if status:
        _fail(f"{label}存在未提交修改，拒绝自动交付")


def _assert_master_worktree_safe() -> None:
    master_worktree = _worktree_for_branch("master")
    if master_worktree is not None:
        _assert_clean_worktree(master_worktree, "本地 master 工作区")


def _assert_delivery_worktrees_clean(label: str) -> None:
    """Reject delivery when either the active or master worktree is dirty."""
    _assert_clean_worktree(BASE_DIR, label)
    _assert_master_worktree_safe()


def _run_local_gate() -> None:
    print("\n>>> 本地交付门禁")
    _run([sys.executable, "tests/run_unit_tests.py"])
    _run([sys.executable, "tests/test_import.py"])
    _run(["git", "diff", "--check", "origin/master...HEAD"])


def preflight(branch: str, *, run_tests: bool = True) -> dict[str, str]:
    """Validate a clean, conflict-free branch based on current origin/master."""
    branch = validate_branch_name(branch)
    if _current_branch() != branch:
        _fail(f"当前检出分支不是 {branch!r}")
    _assert_delivery_worktrees_clean("当前工作区")

    _run(["gh", "auth", "status", "--hostname", "github.com"])
    _run(["git", "fetch", "origin"])
    _run(["git", "fetch", "gitee"])

    head_sha = _git_text("rev-parse", "HEAD")
    master_sha = _git_text("rev-parse", "origin/master")
    if head_sha == master_sha:
        _fail("分支没有需要交付的提交")
    if not _is_ancestor("origin/master", "HEAD"):
        _fail("分支未基于最新 origin/master，需人工处理分叉；自动流程不会 rebase")

    merge_probe = _run(
        ["git", "merge-tree", "--write-tree", "origin/master", "HEAD"],
        check=False,
        capture_output=True,
    )
    if merge_probe.returncode != 0:
        _fail("分支与 origin/master 存在合并冲突，拒绝自动交付")

    if run_tests:
        _run_local_gate()
    _assert_delivery_worktrees_clean("本地门禁后当前工作区")

    commit_count = _git_text("rev-list", "--count", "origin/master..HEAD")
    return {
        "branch": branch,
        "head_sha": head_sha,
        "master_sha": master_sha,
        "commit_count": commit_count,
    }


def _load_json(result: subprocess.CompletedProcess[str]) -> Any:
    try:
        return json.loads(result.stdout or "null")
    except json.JSONDecodeError as error:
        _fail(f"命令返回了无效 JSON：{error}")


def _find_delivery_pr(branch: str, head_sha: str) -> dict[str, Any] | None:
    result = _run(
        [
            "gh", "pr", "list",
            "--head", branch,
            "--base", "master",
            "--state", "all",
            "--limit", "20",
            "--json", "number,state,isDraft,url,headRefOid,mergeCommit",
        ],
        capture_output=True,
    )
    candidates = _load_json(result) or []
    matching = [pr for pr in candidates if pr.get("headRefOid") == head_sha]
    open_prs = [pr for pr in matching if pr.get("state") == "OPEN"]
    if open_prs:
        if open_prs[0].get("isDraft"):
            _fail("已存在的 PR 仍为 Draft，请先转为 Ready for review")
        return open_prs[0]
    merged_prs = [pr for pr in matching if pr.get("state") == "MERGED"]
    if merged_prs:
        return merged_prs[0]
    closed_prs = [pr for pr in matching if pr.get("state") == "CLOSED"]
    if closed_prs:
        _fail("同一提交对应的 PR 已关闭且未合并，拒绝自动创建重复 PR")
    return None


def _default_pr_title() -> str:
    return _git_text("log", "-1", "--format=%s")


def _default_pr_body() -> str:
    commits = _git_text(
        "log", "--reverse", "--format=- %s", "origin/master..HEAD"
    )
    return (
        "## 变更提交\n\n"
        f"{commits}\n\n"
        "## 自动验证\n\n"
        "- 稳定单元回归\n"
        "- 导入烟测\n"
        "- `git diff --check`\n"
        "- PR Checks（合并前等待通过）\n"
    )


def _push_and_create_pr(
    branch: str,
    head_sha: str,
    *,
    title: str | None = None,
) -> dict[str, Any]:
    _run(["git", "push", "-u", "origin", branch])
    pr = _find_delivery_pr(branch, head_sha)
    if pr is not None:
        print(f"  [复用] PR #{pr['number']}: {pr['url']}")
        return pr

    create_args = [
        "gh", "pr", "create",
        "--base", "master",
        "--head", branch,
        "--title", title or _default_pr_title(),
        "--body", _default_pr_body(),
    ]
    create_result: subprocess.CompletedProcess[str] | None = None
    for attempt in range(1, 4):
        create_result = _run(
            create_args,
            check=False,
            capture_output=True,
        )
        if create_result.returncode == 0:
            break
        existing = _find_delivery_pr(branch, head_sha)
        if existing is not None:
            return existing
        if attempt < 3:
            print(f"  [重试] GitHub 暂未接受新分支，稍后重试创建 PR（{attempt + 1}/3）")
            time.sleep(attempt * 2)
    if create_result is None or create_result.returncode != 0:
        detail = str((create_result.stderr or create_result.stdout or "未知错误")).strip()
        _fail(f"PR 创建失败：{detail}")
    print(f"  [OK] PR 已创建: {create_result.stdout.strip()}")
    pr = _find_delivery_pr(branch, head_sha)
    if pr is None:
        _fail("PR 创建后无法读取其状态")
    return pr


def _pr_view(number: int) -> dict[str, Any]:
    result = _run(
        [
            "gh", "pr", "view", str(number),
            "--json",
            "number,state,isDraft,url,mergeable,mergeStateStatus,statusCheckRollup,mergeCommit",
        ],
        capture_output=True,
    )
    return _load_json(result) or {}


def _check_rollup_state(rollup: list[dict[str, Any]]) -> tuple[str, str]:
    if not rollup:
        return "pending", "等待 PR Checks 创建"
    pending: list[str] = []
    failures: list[str] = []
    for check in rollup:
        name = str(check.get("name") or check.get("context") or "未命名检查")
        typename = check.get("__typename")
        if typename == "StatusContext":
            state = str(check.get("state") or "").upper()
            if state == "SUCCESS":
                continue
            if state in ("ERROR", "FAILURE"):
                failures.append(name)
            else:
                pending.append(name)
            continue
        status = str(check.get("status") or "").upper()
        conclusion = str(check.get("conclusion") or "").upper()
        if status != "COMPLETED":
            pending.append(name)
        elif conclusion in FAILURE_CONCLUSIONS or conclusion not in SUCCESS_CONCLUSIONS:
            failures.append(name)
    if failures:
        return "failed", "、".join(failures)
    if pending:
        return "pending", "、".join(pending)
    return "success", "全部检查通过"


def wait_for_pr_checks(
    number: int,
    *,
    timeout: int = DEFAULT_CHECK_TIMEOUT,
    poll_interval: int = DEFAULT_POLL_INTERVAL,
) -> dict[str, Any]:
    """Wait until every reported PR check succeeds and the PR is clean."""
    deadline = time.monotonic() + max(1, timeout)
    while True:
        pr = _pr_view(number)
        if pr.get("state") == "MERGED":
            return pr
        if pr.get("state") != "OPEN":
            _fail(f"PR #{number} 不再处于可交付状态：{pr.get('state')}")
        if pr.get("isDraft"):
            _fail(f"PR #{number} 仍为 Draft")
        check_state, detail = _check_rollup_state(pr.get("statusCheckRollup") or [])
        if check_state == "failed":
            _fail(f"PR #{number} 检查失败：{detail}")
        if pr.get("mergeable") == "CONFLICTING":
            _fail(f"PR #{number} 存在合并冲突")
        if check_state == "success":
            if (
                pr.get("mergeable") == "MERGEABLE"
                and pr.get("mergeStateStatus") == "CLEAN"
            ):
                print(f"  [OK] PR #{number} 检查通过且可合并")
                return pr
            _fail(
                f"PR #{number} 检查通过但合并状态为 "
                f"{pr.get('mergeable')}/{pr.get('mergeStateStatus')}"
            )
        if time.monotonic() >= deadline:
            _fail(f"等待 PR #{number} 检查超时：{detail}")
        print(f"  [等待] PR #{number}: {detail}")
        time.sleep(max(1, poll_interval))


def _merge_pr(pr: dict[str, Any]) -> dict[str, Any]:
    number = int(pr["number"])
    current = _pr_view(number)
    if current.get("state") != "MERGED":
        _run(["gh", "pr", "merge", str(number), "--squash"])
        current = _pr_view(number)
    if current.get("state") != "MERGED":
        _fail(f"PR #{number} 合并命令完成后状态仍不是 MERGED")
    merge_commit = (current.get("mergeCommit") or {}).get("oid")
    if not merge_commit:
        _fail(f"PR #{number} 缺少合并提交信息")
    print(f"  [OK] PR #{number} 已合并: {merge_commit[:12]}")
    return current


def _update_local_master(expected_sha: str) -> bool:
    """Update local master and report whether it is checked out elsewhere."""
    master_worktree = _worktree_for_branch("master")
    if master_worktree is not None:
        _assert_clean_worktree(master_worktree, "本地 master 工作区")
        _run(
            ["git", "pull", "--ff-only", "origin", "master"],
            cwd=master_worktree,
        )
    else:
        _run(["git", "branch", "-f", "master", "origin/master"])
    if _git_text("rev-parse", "master") != expected_sha:
        _fail("本地 master 未能快进到合并提交")
    return master_worktree is not None


def finalize_delivery(branch: str, merge_sha: str) -> dict[str, str]:
    """Sync both masters, then remove only the delivered topic branch."""
    _assert_delivery_worktrees_clean("PR 合并后当前工作区")
    _run(["git", "fetch", "origin"])
    origin_master = _remote_ref("origin", "refs/heads/master")
    if origin_master != merge_sha:
        _fail("GitHub master 与 PR 合并提交不一致，拒绝同步和清理")

    _run(["git", "push", "gitee", "origin/master:master"])
    gitee_master = _remote_ref("gitee", "refs/heads/master")
    if gitee_master != merge_sha:
        _fail("Gitee master 与 GitHub master 不一致，拒绝清理分支")

    master_checked_out_elsewhere = _update_local_master(merge_sha)
    _assert_delivery_worktrees_clean("分支清理前当前工作区")

    if _remote_branch_exists("origin", branch):
        _run(["git", "push", "origin", "--delete", branch])
    if _current_branch() == branch:
        if master_checked_out_elsewhere:
            _run(["git", "switch", "--detach", "origin/master"])
        else:
            _run(["git", "switch", "master"])
    if _local_branch_exists(branch):
        _run(["git", "branch", "-D", branch])

    if _remote_branch_exists("origin", branch) or _local_branch_exists(branch):
        _fail("交付分支清理后仍然存在")
    print("  [OK] GitHub/Gitee master 已同步，本地 master 已更新，交付分支已清理")
    return {
        "merge_sha": merge_sha,
        "origin_master": origin_master,
        "gitee_master": gitee_master,
    }


def deliver(
    branch: str,
    *,
    execute: bool = False,
    authorization: str = "",
    title: str | None = None,
    run_local_tests: bool = True,
    timeout: int = DEFAULT_CHECK_TIMEOUT,
    poll_interval: int = DEFAULT_POLL_INTERVAL,
) -> dict[str, Any]:
    """Preview or execute the complete ordinary-PR delivery chain."""
    branch = validate_branch_name(branch)
    if execute:
        validate_authorization(branch, authorization)

    head_sha = _git_text("rev-parse", branch)
    existing_pr = _find_delivery_pr(branch, head_sha)
    if existing_pr and existing_pr.get("state") == "MERGED":
        if not execute:
            print(f"[预览] PR #{existing_pr['number']} 已合并，执行模式将完成同步和清理")
            return {"mode": "preview", "pr": existing_pr}
        merge_sha = (existing_pr.get("mergeCommit") or {}).get("oid")
        if not merge_sha:
            merge_sha = (_pr_view(int(existing_pr["number"])).get("mergeCommit") or {}).get("oid")
        if not merge_sha:
            _fail("已合并 PR 缺少合并提交信息")
        result = finalize_delivery(branch, merge_sha)
        return {"mode": "execute", "pr": existing_pr, **result}

    gate = (
        preflight(branch)
        if run_local_tests
        else preflight(branch, run_tests=False)
    )
    if not execute:
        print("\n[预览] 本地门禁通过；未 push、未创建 PR、未合并、未清理")
        print(f"  执行授权: {expected_authorization(branch)}")
        return {"mode": "preview", "gate": gate}

    print("\n>>> 推送并创建/复用 PR")
    pr = _push_and_create_pr(branch, gate["head_sha"], title=title)
    pr = wait_for_pr_checks(
        int(pr["number"]), timeout=timeout, poll_interval=poll_interval
    )
    merged = _merge_pr(pr)
    merge_sha = (merged.get("mergeCommit") or {}).get("oid")
    result = finalize_delivery(branch, merge_sha)
    return {"mode": "execute", "pr": merged, **result}


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="普通 PR 一次授权交付")
    parser.add_argument("--branch", help="codex/<task>；默认使用当前分支")
    parser.add_argument("--title", help="新建 PR 时使用的标题；默认取最新提交标题")
    parser.add_argument("--execute", action="store_true", help="执行外部写操作；默认仅预览")
    parser.add_argument("--authorization", default="", help="精确授权文本")
    parser.add_argument("--timeout", type=int, default=DEFAULT_CHECK_TIMEOUT)
    parser.add_argument("--poll-interval", type=int, default=DEFAULT_POLL_INTERVAL)
    args = parser.parse_args(argv)

    branch = args.branch or _current_branch()
    try:
        result = deliver(
            branch,
            execute=args.execute,
            authorization=args.authorization,
            title=args.title,
            timeout=args.timeout,
            poll_interval=args.poll_interval,
        )
    except (PRDeliveryError, subprocess.CalledProcessError) as error:
        print(f"\n[失败] {error}", file=sys.stderr)
        return 1

    if result.get("mode") == "execute":
        print(f"\n[OK] 一键交付完成: {result.get('merge_sha', '')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
