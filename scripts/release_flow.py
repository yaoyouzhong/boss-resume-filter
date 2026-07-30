"""Single user-facing, resumable release flow for one or more topic branches.

The start authorization prepares and validates a release-candidate PR, then
stops after printing the exact user-facing release content. Confirmation
verifies the immutable candidate evidence, Squash merges the PR, and reuses the
existing formal-release driver through public verification.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any


BASE_DIR = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = Path(__file__).resolve().parent
for import_path in (BASE_DIR, SCRIPTS_DIR):
    if str(import_path) not in sys.path:
        sys.path.insert(0, str(import_path))

import pr_delivery  # noqa: E402
import build  # noqa: E402
import release_content_review  # noqa: E402
import release_dispatch  # noqa: E402
import release_prepare  # noqa: E402
from subprocess_utils import hidden_subprocess  # noqa: E402

subprocess = hidden_subprocess(subprocess)


STATE_PATH = BASE_DIR / ".release_flow_state.json"
STATE_SCHEMA = 1


class ReleaseFlowError(RuntimeError):
    """The unified release transaction cannot safely continue."""


def _fail(message: str) -> None:
    raise ReleaseFlowError(message)


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


def _git_text(*args: str) -> str:
    return _run(["git", *args], capture_output=True).stdout.strip()


def _tree_sha(ref: str) -> str:
    return _git_text("rev-parse", f"{ref}^{{tree}}")


def _local_codex_branches() -> list[str]:
    output = _git_text(
        "for-each-ref",
        "--format=%(refname:short)",
        "refs/heads/codex/",
    )
    return sorted(line.strip() for line in output.splitlines() if line.strip())


def _discover_cleanup_branches(
    candidate_branch: str,
    candidate_sha: str,
    base_sha: str,
    source_branches: list[str],
) -> list[dict[str, str]]:
    """Record release-contained topic branches that can be cleaned later."""
    candidate_branch = pr_delivery.validate_branch_name(candidate_branch)
    explicit_sources = {
        pr_delivery.validate_branch_name(branch) for branch in source_branches
    }
    included: list[dict[str, str]] = []
    for branch in _local_codex_branches():
        pr_delivery.validate_branch_name(branch)
        if branch == candidate_branch:
            continue
        if not pr_delivery._is_ancestor(branch, candidate_sha):
            continue
        if branch not in explicit_sources and pr_delivery._is_ancestor(
            branch, base_sha,
        ):
            continue
        included.append({
            "branch": branch,
            "head_sha": _git_text("rev-parse", branch),
            "role": "included",
        })
    included.append({
        "branch": candidate_branch,
        "head_sha": candidate_sha,
        "role": "candidate",
    })
    return included


def _cleanup_entries(state: dict[str, Any]) -> list[dict[str, str]]:
    raw_entries = state.get("cleanup_branches")
    if raw_entries is None:
        raw_entries = [{
            "branch": state["candidate_branch"],
            "head_sha": state["candidate_sha"],
            "role": "candidate",
        }]
    if not isinstance(raw_entries, list) or not raw_entries:
        _fail("发布分支清理计划为空或格式无效")

    entries: list[dict[str, str]] = []
    seen: set[str] = set()
    for raw in raw_entries:
        if not isinstance(raw, dict):
            _fail("发布分支清理计划格式无效")
        branch = pr_delivery.validate_branch_name(str(raw.get("branch") or ""))
        head_sha = str(raw.get("head_sha") or "")
        role = str(raw.get("role") or "included")
        if not re.fullmatch(r"[0-9a-fA-F]{40}", head_sha):
            _fail(f"发布分支清理计划缺少完整提交：{branch}")
        if role not in {"candidate", "included"}:
            _fail(f"发布分支清理计划角色无效：{branch}")
        if branch in seen:
            _fail(f"发布分支清理计划包含重复项：{branch}")
        seen.add(branch)
        entries.append({
            "branch": branch,
            "head_sha": head_sha.lower(),
            "role": role,
        })

    candidate = str(state["candidate_branch"])
    if [item["branch"] for item in entries if item["role"] == "candidate"] != [candidate]:
        _fail("发布分支清理计划中的候选分支不一致")
    return entries


def _validate_cleanup_plan(state: dict[str, Any]) -> list[dict[str, str]]:
    """Fail closed before deleting any release-contained branch."""
    entries = _cleanup_entries(state)
    candidate_sha = str(state["candidate_sha"])
    merge_sha = str(state.get("merge_sha") or "")
    if merge_sha:
        origin_master = pr_delivery._remote_ref("origin", "refs/heads/master")
        gitee_master = pr_delivery._remote_ref("gitee", "refs/heads/master")
        if not origin_master or origin_master != gitee_master:
            _fail("分支清理前 GitHub/Gitee master 不一致")
        if merge_sha != origin_master and not pr_delivery._is_ancestor(
            merge_sha, origin_master,
        ):
            _fail("分支清理前发布合并提交已不在当前 master 历史中")
    for entry in entries:
        branch = entry["branch"]
        expected_sha = entry["head_sha"]
        if not pr_delivery._is_ancestor(expected_sha, candidate_sha):
            _fail(f"待清理分支未包含在已确认候选中：{branch}")
        if pr_delivery._local_branch_exists(branch):
            current_sha = _git_text("rev-parse", branch)
            if current_sha != expected_sha:
                _fail(f"待清理分支在候选确认后发生变化，已保留：{branch}")
        worktree = pr_delivery._worktree_for_branch(branch)
        if worktree is not None:
            pr_delivery._assert_clean_worktree(
                worktree, f"待清理分支 {branch} 工作区",
            )
        for remote in ("origin", "gitee"):
            remote_sha = pr_delivery._remote_ref(
                remote, f"refs/heads/{branch}",
            )
            if remote_sha and remote_sha != expected_sha:
                _fail(
                    f"{remote} 待清理分支在候选确认后发生变化，已保留：{branch}"
                )
    return entries


def _delete_included_branch(entry: dict[str, str]) -> None:
    branch = entry["branch"]
    worktree = pr_delivery._worktree_for_branch(branch)
    if worktree is not None:
        _run(["git", "switch", "--detach", "origin/master"], cwd=worktree)
    for remote in ("origin", "gitee"):
        if pr_delivery._remote_branch_exists(remote, branch):
            pr_delivery._run_external(
                ["git", "push", remote, "--delete", branch],
                f"删除 {remote} 已发布来源分支 {branch}",
                postcondition=lambda remote=remote: not (
                    pr_delivery._remote_branch_exists(remote, branch)
                ),
            )
    if pr_delivery._local_branch_exists(branch):
        _run(["git", "branch", "-D", branch])
    if (
        pr_delivery._local_branch_exists(branch)
        or pr_delivery._remote_branch_exists("origin", branch)
        or pr_delivery._remote_branch_exists("gitee", branch)
    ):
        _fail(f"已发布来源分支清理后仍然存在：{branch}")
    print(f"  [OK] 已清理候选包含分支：{branch}")


def _cleanup_release_branches(state: dict[str, Any]) -> None:
    entries = _validate_cleanup_plan(state)
    candidate = next(item for item in entries if item["role"] == "candidate")
    for entry in entries:
        if entry["role"] == "included":
            _delete_included_branch(entry)
    pr_delivery.cleanup_delivered_branch(
        candidate["branch"], str(state["merge_sha"]),
    )
    if pr_delivery._remote_branch_exists("gitee", candidate["branch"]):
        pr_delivery._run_external(
            ["git", "push", "gitee", "--delete", candidate["branch"]],
            f"删除 gitee 发布候选分支 {candidate['branch']}",
            postcondition=lambda: not pr_delivery._remote_branch_exists(
                "gitee", candidate["branch"],
            ),
        )


def _read_state() -> dict[str, Any]:
    if not STATE_PATH.is_file():
        _fail("没有可确认的一键发布状态；请先准备发布候选")
    try:
        state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        _fail(f"一键发布状态文件损坏：{exc}")
    if state.get("schema") != STATE_SCHEMA:
        _fail("一键发布状态版本不兼容，请人工检查后重新准备")
    return state


def _write_state(state: dict[str, Any]) -> None:
    payload = {"schema": STATE_SCHEMA, **state}
    temporary = STATE_PATH.with_suffix(STATE_PATH.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, STATE_PATH)


@contextmanager
def _timed_step(label: str):
    started = time.perf_counter()
    print(f"\n>>> {label}...")
    try:
        yield
    finally:
        elapsed = time.perf_counter() - started
        print(f"  [耗时] {label}: {elapsed:.1f}s")


def expected_start_authorization(version: str, branches: list[str]) -> str:
    version = release_prepare.normalize_version(version)
    if len(branches) <= 1:
        return f"一键发布版本 v{version}"
    return f"一键发布版本 v{version}，包含 " + "、".join(branches)


def expected_confirm_authorization(version: str) -> str:
    return f"确认发布 v{release_prepare.normalize_version(version)}"


def _validate_authorization(actual: str, expected: str, label: str) -> None:
    if actual != expected:
        _fail(f"{label}授权不匹配：必须准确填写 {expected!r}")


def _validate_notes_file(notes_file: Path | None) -> Path:
    if notes_file is None:
        _fail("准备发布候选必须提供 --notes-file")
    path = notes_file if notes_file.is_absolute() else BASE_DIR / notes_file
    path = path.resolve()
    if path == BASE_DIR or BASE_DIR in path.parents:
        _fail("发布说明是临时输入文件，必须放在项目目录之外")
    if not path.is_file():
        _fail(f"发布说明文件不存在：{path}")
    return path


def _assert_clean(label: str = "当前工作区") -> None:
    if _git_text("status", "--porcelain"):
        _fail(f"{label}存在未提交修改")


def _fetch_and_verify_masters() -> str:
    pr_delivery._run_external(["git", "fetch", "origin"], "拉取 GitHub 更新")
    pr_delivery._run_external(["git", "fetch", "gitee"], "拉取 Gitee 更新")
    origin = _git_text("rev-parse", "origin/master")
    gitee = _git_text("rev-parse", "gitee/master")
    if origin != gitee:
        _fail("GitHub/Gitee master 不一致，拒绝准备发布")
    return origin


def _tested_map(values: list[str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for value in values:
        branch, separator, sha = value.partition("=")
        if not separator or not branch or not sha:
            _fail("--tested-branch 必须使用 branch=commit_sha 格式")
        if not re.fullmatch(r"[0-9a-fA-F]{40}", sha):
            _fail("--tested-branch 必须记录完整的 40 位 commit SHA")
        result[pr_delivery.validate_branch_name(branch)] = sha
    return result


def _validate_source_branches(branches: list[str], tested: dict[str, str]) -> None:
    for branch in branches:
        pr_delivery.validate_branch_name(branch)
        if not pr_delivery._local_branch_exists(branch):
            _fail(f"本地分支不存在：{branch}")
        worktree = pr_delivery._worktree_for_branch(branch)
        if worktree is None:
            _fail(f"分支 {branch} 没有独立 worktree，无法在其自身目录验证")
        pr_delivery._assert_clean_worktree(worktree, f"分支 {branch} 工作区")
        head = _git_text("rev-parse", branch)
        if tested.get(branch) != head:
            _fail(f"分支 {branch} 缺少与当前提交一致的 GUI 实测凭证：{head}")
        print(f"\n>>> 分支独立回归：{branch} ({worktree})")
        _run([sys.executable, "tests/run_unit_tests.py"], cwd=worktree)
        _run([sys.executable, "tests/test_import.py"], cwd=worktree)
        pr_delivery._assert_clean_worktree(worktree, f"分支 {branch} 测试后工作区")


def _prepare_aggregate_branch(
    version: str,
    branches: list[str],
    tested: dict[str, str],
    master_sha: str,
) -> str:
    _validate_source_branches(branches, tested)
    branch = release_prepare.release_branch(version)
    if pr_delivery._local_branch_exists(branch):
        if _git_text("branch", "--show-current") != branch:
            _fail(f"聚合分支 {branch} 已存在，请在该分支续跑或人工检查")
        missing = [
            source for source in branches
            if not pr_delivery._is_ancestor(source, branch)
        ]
        if missing:
            _fail("现有聚合分支未完整包含：" + "、".join(missing))
        return branch
    if _git_text("branch", "--show-current") != "master":
        _fail("多分支聚合必须从本地 master 启动")
    if _git_text("rev-parse", "HEAD") != master_sha:
        _fail("本地 master 不是最新 origin/master")
    _run(["git", "switch", "-c", branch, "origin/master"])
    for source in branches:
        result = _run(
            ["git", "merge", "--no-ff", "--no-edit", source],
            check=False,
            capture_output=True,
        )
        if result.returncode != 0:
            _fail(f"聚合分支 {source} 时发生冲突；已停止且不会自动解决")
    return branch


def _prepare_single_branch(branches: list[str], master_sha: str) -> str:
    branch = branches[0] if branches else _git_text("branch", "--show-current")
    branch = pr_delivery.validate_branch_name(branch)
    if _git_text("branch", "--show-current") != branch:
        _fail(f"单分支发布必须在 {branch} 对应目录执行")
    if not pr_delivery._is_ancestor("origin/master", "HEAD"):
        _fail("开发分支未基于最新 origin/master，自动流程不会 rebase")
    if _git_text("rev-parse", "HEAD") == master_sha:
        _fail("开发分支没有需要发布的提交")
    return branch


def _apply_release_materials(version: str, notes_path: Path) -> None:
    title, body = release_prepare.parse_release_notes(
        notes_path.read_text(encoding="utf-8"), version,
    )
    release_prepare.apply_release_materials(version, title, body)
    changed = release_prepare._status_paths()
    unexpected = changed - release_prepare.RELEASE_FILES
    if unexpected:
        _fail("发布材料阶段出现意外修改：" + ", ".join(sorted(unexpected)))
    review = release_content_review.review_release_worktree(version)
    print("\n>>> 严格门禁前版本内容预审")
    print(f"  标题: {review['release_title']}")
    print(review["release_body"])
    if changed:
        release_prepare._run_strict_gate()
        _run(["git", "add", *sorted(release_prepare.RELEASE_FILES)])
        _run(["git", "diff", "--cached", "--check"])
        staged = _run(
            ["git", "diff", "--cached", "--quiet"],
            check=False,
        )
        if staged.returncode == 1:
            _run(["git", "commit", "-m", f"chore: 准备 v{version} 正式发布"])
        elif staged.returncode != 0:
            _fail("无法确认发布材料的暂存状态")
    else:
        release_prepare._run_strict_gate()


def _print_candidate(state: dict[str, Any]) -> None:
    print(f"\n>>> v{state['version']} 最终版本内容（等待人工确认）")
    print(f"  候选分支: {state['candidate_branch']}")
    print(f"  候选提交: {state['candidate_sha']}")
    print(f"  候选 tree: {state['candidate_tree_sha']}")
    print(f"  PR: {state['pr_url']}")
    print(f"  标题: {state['release_title']}")
    print(state["release_body"])
    cleanup = "、".join(
        item["branch"] for item in _cleanup_entries(state)
    )
    print(f"\n  公开验收后自动清理分支: {cleanup}")
    print(f"\n  内部内容凭证: {state['content_sha'][:12]}")
    print(f"  确认口令: {expected_confirm_authorization(state['version'])}")


def _verify_completed_release_receipt(state: dict[str, Any]) -> None:
    """Reject a stale local complete marker without repeating full acceptance."""
    version = str(state["version"])
    formal = dict(state.get("formal_release") or {})
    verification = dict(formal.get("verification") or {})
    expected_master = str(
        verification.get("manifest_sha")
        or formal.get("master_sha")
        or ""
    )
    origin_master = build._remote_ref_commit("origin", "refs/heads/master")
    gitee_master = build._remote_ref_commit("gitee", "refs/heads/master")
    if not expected_master or origin_master != expected_master or gitee_master != expected_master:
        _fail("本地发布完成凭证与当前 GitHub/Gitee master 不一致")
    tag = f"v{version}"
    origin_tag = build._remote_tag_commit("origin", tag)
    gitee_tag = build._remote_tag_commit("gitee", tag)
    if origin_tag != state.get("merge_sha") or gitee_tag != state.get("merge_sha"):
        _fail("本地发布完成凭证与当前双远端 tag 不一致")
    github_release = build._get_github_release_info(tag)
    if not github_release or github_release.get("isDraft"):
        _fail("本地发布完成凭证对应的 GitHub Release 不存在或仍为草稿")
    if not build._get_gitee_release_read_only(version):
        _fail("本地发布完成凭证对应的 Gitee Release 不存在")
    print("  [OK] 本地完成凭证与双远端 master、tag 和 Release 一致")


def prepare_candidate(
    version: str,
    *,
    notes_file: Path | None,
    branches: list[str],
    tested_branches: list[str],
    authorization: str,
    timeout: int,
    poll_interval: int,
) -> dict[str, Any]:
    """Prepare or update one candidate PR and stop before merge."""
    version = release_prepare.normalize_version(version)
    normalized = [pr_delivery.validate_branch_name(item) for item in branches]
    if len(set(normalized)) != len(normalized):
        _fail("发布分支列表包含重复项")
    effective = normalized or [_git_text("branch", "--show-current")]
    _validate_authorization(
        authorization,
        expected_start_authorization(version, effective),
        "一键发布",
    )
    with _timed_step("校验授权、说明文件与工作区"):
        notes_path = _validate_notes_file(notes_file)
        _assert_clean()
    with _timed_step("拉取并核验 GitHub/Gitee master"):
        master_sha = _fetch_and_verify_masters()
    with _timed_step("核验目标版本 tag"):
        release_prepare.assert_target_tag_available(version)
    with _timed_step("准备候选分支"):
        if len(effective) > 1:
            candidate_branch = _prepare_aggregate_branch(
                version, effective, _tested_map(tested_branches), master_sha,
            )
        else:
            candidate_branch = _prepare_single_branch(effective, master_sha)

    with _timed_step("同步发布材料并运行严格门禁"):
        _apply_release_materials(version, notes_path)
    candidate_sha = _git_text("rev-parse", "HEAD")
    candidate_tree = _tree_sha(candidate_sha)
    cleanup_branches = _discover_cleanup_branches(
        candidate_branch, candidate_sha, master_sha, effective,
    )
    with _timed_step("生成版本内容审核"):
        review = release_content_review.review_release_candidate(
            version, candidate_sha, candidate_tree,
        )
    with _timed_step("本地 PR 预检"):
        gate = pr_delivery.preflight(candidate_branch, run_tests=False)
    with _timed_step("推送并创建或复用 PR"):
        pr = pr_delivery._push_and_create_pr(
            candidate_branch,
            gate["head_sha"],
            title=f"chore: 准备 v{version} 正式发布",
        )
    with _timed_step("等待 PR Checks"):
        checked = pr_delivery.wait_for_pr_checks(
            int(pr["number"]),
            timeout=timeout,
            poll_interval=poll_interval,
            expected_head_sha=gate["head_sha"],
        )
    candidate_sha = gate["head_sha"]
    if candidate_sha != review["candidate_sha"]:
        _fail("版本内容审核后候选提交发生变化")
    state = {
        "phase": "awaiting_content_approval",
        "version": version,
        "source_branches": effective,
        "tested_branches": _tested_map(tested_branches),
        "candidate_branch": candidate_branch,
        "candidate_sha": candidate_sha,
        "candidate_tree_sha": candidate_tree,
        "cleanup_branches": cleanup_branches,
        "base_sha": master_sha,
        "pr_number": int(checked["number"]),
        "pr_url": checked.get("url") or pr.get("url") or "",
        **review,
    }
    _write_state(state)
    _print_candidate(state)
    return state


def _verify_candidate(
    state: dict[str, Any],
    *,
    timeout: int,
    poll_interval: int,
) -> dict[str, Any]:
    branch = str(state["candidate_branch"])
    sha = str(state["candidate_sha"])
    if _git_text("branch", "--show-current") != branch:
        _fail(f"内容确认必须在候选分支 {branch} 的原 worktree 执行")
    _assert_clean("内容确认前当前工作区")
    if _git_text("rev-parse", branch) != sha:
        _fail("候选分支提交在确认前发生变化；必须重新展示版本内容")
    if _tree_sha(sha) != state["candidate_tree_sha"]:
        _fail("候选文件树在确认前发生变化；必须重新展示版本内容")
    if "cleanup_branches" in state:
        _validate_cleanup_plan(state)
    review = release_content_review.review_release_candidate(
        state["version"], sha, state["candidate_tree_sha"],
    )
    release_content_review.require_approved_content(review, state["content_sha"])
    pr = pr_delivery.wait_for_pr_checks(
        int(state["pr_number"]),
        timeout=timeout,
        poll_interval=poll_interval,
        expected_head_sha=sha,
    )
    if pr.get("headRefOid") != sha:
        _fail("PR head 在确认前发生变化；必须重新展示版本内容")
    if pr.get("baseRefOid") and pr.get("baseRefOid") != state["base_sha"]:
        _fail("PR 目标 master 在确认前发生变化；必须重新准备并确认")
    return pr


def _dispatch_formal_release(
    version: str,
    approved_content_sha: str,
) -> dict[str, Any]:
    """Run formal publication from the worktree that actually owns master."""
    if _git_text("branch", "--show-current") == "master":
        return release_dispatch.dispatch_release(
            version,
            execute=True,
            authorization=release_dispatch.expected_authorization(version),
            approved_content_sha=approved_content_sha,
        )
    master_worktree = pr_delivery._worktree_for_branch("master")
    if master_worktree is None:
        _assert_clean("正式发布切换 master 前当前工作区")
        _run(["git", "switch", "master"])
        if _git_text("branch", "--show-current") != "master":
            _fail("合并后无法切换到可用于正式发布的 master 工作区")
        return release_dispatch.dispatch_release(
            version,
            execute=True,
            authorization=release_dispatch.expected_authorization(version),
            approved_content_sha=approved_content_sha,
        )
    script = master_worktree / "scripts" / "release_dispatch.py"
    result = subprocess.run(
        [
            sys.executable,
            str(script),
            "--version", version,
            "--execute",
            "--authorization", release_dispatch.expected_authorization(version),
            "--approved-content-sha", approved_content_sha,
        ],
        cwd=master_worktree,
        check=False,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode != 0:
        _fail(f"master 工作区正式发布失败（退出码 {result.returncode}）")
    release_state: dict[str, Any] = {}
    release_state_path = master_worktree / ".release_state.json"
    try:
        parsed = json.loads(release_state_path.read_text(encoding="utf-8"))
        if isinstance(parsed, dict):
            release_state = parsed
    except (OSError, json.JSONDecodeError):
        pass
    verification = (
        ((release_state.get("phases") or {}).get("public_verification") or {})
        .get("details")
        or {}
    )
    return {
        "mode": "published_from_master_worktree",
        "path": str(master_worktree),
        "actions_run": release_state.get("actions_run"),
        "verification": verification,
    }


def confirm_release(
    version: str,
    authorization: str,
    *,
    approved_content_sha: str,
    timeout: int = pr_delivery.DEFAULT_CHECK_TIMEOUT,
    poll_interval: int = pr_delivery.DEFAULT_POLL_INTERVAL,
) -> dict[str, Any]:
    """Validate candidate approval, merge it, then publish to completion."""
    version = release_prepare.normalize_version(version)
    _validate_authorization(
        authorization, expected_confirm_authorization(version), "正式发布",
    )
    state = _read_state()
    if state.get("version") != version:
        _fail("待确认状态与目标版本不一致")
    release_content_review.require_approved_content(state, approved_content_sha)
    if state.get("phase") == "complete":
        _verify_completed_release_receipt(state)
        return state
    origin_fetched_after_merge = False
    if state.get("phase") == "awaiting_content_approval":
        pr = _verify_candidate(
            state, timeout=timeout, poll_interval=poll_interval,
        )
        merged = pr_delivery._merge_pr(pr)
        merge_sha = (merged.get("mergeCommit") or {}).get("oid")
        if not merge_sha:
            _fail("候选 PR 合并后缺少提交信息")
        pr_delivery._run_external(["git", "fetch", "origin"], "拉取 GitHub 合并结果")
        origin_fetched_after_merge = True
        if _tree_sha(merge_sha) != state["candidate_tree_sha"]:
            _fail("Squash 合并后的文件树与已确认候选不一致，禁止正式发布")
        state.update({"phase": "merged_pending_sync", "merge_sha": merge_sha})
        _write_state(state)
    if state.get("phase") == "merged_pending_sync":
        pr_delivery.synchronize_merged_delivery(
            state["merge_sha"],
            origin_already_fetched=origin_fetched_after_merge,
        )
        state["phase"] = "merged_synced"
        _write_state(state)

    if state.get("phase") in {"merged", "merged_synced"}:
        formal_review = release_content_review.review_release_content(
            version, state["merge_sha"],
        )
        result = _dispatch_formal_release(version, formal_review["content_sha"])
        state.update({
            "phase": "published_pending_cleanup",
            "formal_release": result,
        })
        _write_state(state)

    if state.get("phase") == "published_pending_cleanup":
        _cleanup_release_branches(state)
        state["phase"] = "complete"
        _write_state(state)
    return state


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="单/多分支一键发布统一入口")
    parser.add_argument("--version", required=True, help="目标版本，不带 v 前缀")
    parser.add_argument("--notes-file", type=Path, help="项目外 UTF-8 发布说明")
    parser.add_argument("--branch", action="append", default=[], help="显式纳入的 codex 分支；可重复")
    parser.add_argument(
        "--tested-branch", action="append", default=[],
        help="多分支 GUI 实测凭证 branch=commit_sha；可重复",
    )
    parser.add_argument("--execute", action="store_true", help="准备并推送发布候选 PR")
    parser.add_argument("--confirm", action="store_true", help="确认候选内容并正式发布")
    parser.add_argument(
        "--approved-content-sha", default="",
        help="候选预览生成并由调用方后台传入的内容凭证",
    )
    parser.add_argument("--authorization", default="", help="精确授权文本")
    parser.add_argument("--timeout", type=int, default=pr_delivery.DEFAULT_CHECK_TIMEOUT)
    parser.add_argument("--poll-interval", type=int, default=pr_delivery.DEFAULT_POLL_INTERVAL)
    return parser


def main() -> int:
    release_prepare.build.run_in_venv(__file__)
    args = _build_parser().parse_args()
    try:
        if args.confirm:
            confirm_release(
                args.version,
                args.authorization,
                approved_content_sha=args.approved_content_sha,
                timeout=args.timeout,
                poll_interval=args.poll_interval,
            )
        elif args.execute:
            prepare_candidate(
                args.version,
                notes_file=args.notes_file,
                branches=args.branch,
                tested_branches=args.tested_branch,
                authorization=args.authorization,
                timeout=args.timeout,
                poll_interval=args.poll_interval,
            )
        else:
            _fail("必须指定 --execute 准备候选，或使用 --confirm 继续正式发布")
    except (
        ReleaseFlowError,
        release_prepare.ReleasePreparationError,
        release_content_review.ReleaseContentReviewError,
        pr_delivery.PRDeliveryError,
        release_dispatch.ReleaseDispatchError,
        subprocess.CalledProcessError,
    ) as exc:
        print(f"\n[失败] {exc}", file=sys.stderr)
        return 1
    print("\n[OK] 一键发布流程当前阶段完成")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
