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
from pathlib import Path
from typing import Any


BASE_DIR = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = Path(__file__).resolve().parent
for import_path in (BASE_DIR, SCRIPTS_DIR):
    if str(import_path) not in sys.path:
        sys.path.insert(0, str(import_path))

import pr_delivery  # noqa: E402
import release_content_review  # noqa: E402
import release_dispatch  # noqa: E402
import release_prepare  # noqa: E402


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
    print(f"\n  内部内容凭证: {state['content_sha'][:12]}")
    print(f"  确认口令: {expected_confirm_authorization(state['version'])}")


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
    notes_path = _validate_notes_file(notes_file)
    _assert_clean()
    master_sha = _fetch_and_verify_masters()
    release_prepare.assert_target_tag_available(version)
    if len(effective) > 1:
        candidate_branch = _prepare_aggregate_branch(
            version, effective, _tested_map(tested_branches), master_sha,
        )
    else:
        candidate_branch = _prepare_single_branch(effective, master_sha)

    _apply_release_materials(version, notes_path)
    gate = pr_delivery.preflight(candidate_branch, run_tests=False)
    pr = pr_delivery._push_and_create_pr(
        candidate_branch,
        gate["head_sha"],
        title=f"chore: 准备 v{version} 正式发布",
    )
    checked = pr_delivery.wait_for_pr_checks(
        int(pr["number"]), timeout=timeout, poll_interval=poll_interval,
    )
    candidate_sha = gate["head_sha"]
    candidate_tree = _tree_sha(candidate_sha)
    review = release_content_review.review_release_candidate(
        version, candidate_sha, candidate_tree,
    )
    state = {
        "phase": "awaiting_content_approval",
        "version": version,
        "source_branches": effective,
        "tested_branches": _tested_map(tested_branches),
        "candidate_branch": candidate_branch,
        "candidate_sha": candidate_sha,
        "candidate_tree_sha": candidate_tree,
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
    review = release_content_review.review_release_candidate(
        state["version"], sha, state["candidate_tree_sha"],
    )
    release_content_review.require_approved_content(review, state["content_sha"])
    pr = pr_delivery.wait_for_pr_checks(
        int(state["pr_number"]), timeout=timeout, poll_interval=poll_interval,
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
        _fail("合并后找不到可用于正式发布的 master 工作区")
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
    return {"mode": "published_from_master_worktree", "path": str(master_worktree)}


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
        return state
    if state.get("phase") == "awaiting_content_approval":
        pr = _verify_candidate(
            state, timeout=timeout, poll_interval=poll_interval,
        )
        merged = pr_delivery._merge_pr(pr)
        merge_sha = (merged.get("mergeCommit") or {}).get("oid")
        if not merge_sha:
            _fail("候选 PR 合并后缺少提交信息")
        pr_delivery._run_external(["git", "fetch", "origin"], "拉取 GitHub 合并结果")
        if _tree_sha(merge_sha) != state["candidate_tree_sha"]:
            _fail("Squash 合并后的文件树与已确认候选不一致，禁止正式发布")
        state.update({"phase": "merged_pending_sync", "merge_sha": merge_sha})
        _write_state(state)
    if state.get("phase") == "merged_pending_sync":
        pr_delivery.finalize_delivery(state["candidate_branch"], state["merge_sha"])
        state["phase"] = "merged"
        _write_state(state)

    formal_review = release_content_review.review_release_content(
        version, state["merge_sha"],
    )
    result = _dispatch_formal_release(version, formal_review["content_sha"])
    state.update({"phase": "complete", "formal_release": result})
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
