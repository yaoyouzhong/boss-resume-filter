"""Prepare and deliver one release-preparation pull request.

The default mode is mutation-free.  Execution requires the exact authorization
text ``一键准备并交付版本 vX.Y`` and then composes the existing deterministic
``release_prepare`` and ``pr_delivery`` transactions.  It does not create a
tag, build artifacts, or publish a formal release; those actions remain behind
the separate ``正式发布 vX.Y`` authorization handled by ``release_dispatch``.
"""
from __future__ import annotations

import argparse
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
import release_prepare  # noqa: E402


class ReleaseDeliveryError(RuntimeError):
    """The combined release-preparation delivery contract was not satisfied."""


def _fail(message: str) -> None:
    raise ReleaseDeliveryError(message)


def expected_authorization(version: str) -> str:
    """Return the exact authorization for preparation plus PR delivery."""
    version = release_prepare.normalize_version(version)
    return f"一键准备并交付版本 v{version}"


def validate_authorization(version: str, authorization: str) -> None:
    expected = expected_authorization(version)
    if authorization != expected:
        _fail(f"版本准备交付授权不匹配：必须准确填写 {expected!r}")


def _print_preview(plan: dict[str, Any]) -> None:
    version = plan["version"]
    print(f"\n>>> v{version} 一键准备并交付预览")
    print(f"  当前状态: {plan['state']}")
    print(f"  基准版本: v{plan['base_version']} ({plan['last_tag']})")
    print(f"  发布分支: {plan['release_branch']}")
    print(f"  master: {plan['master_sha']}")
    print(f"  变更提交: {len(plan['commits'])}")
    for commit in plan["commits"]:
        print(f"    - {commit['sha'][:8]} {commit['subject']}")
    print(f"  变更文件: {len(plan['changed_files'])}")
    for path in plan["changed_files"]:
        print(f"    - {path}")
    print("\n执行后将依次完成：")
    print("  1. 同步版本号、CHANGELOG 和用户文档")
    print("  2. 执行严格发布门禁并创建发布准备提交")
    print("  3. 推送分支、创建/复用 PR、等待 CI 并 Squash 合并")
    print("  4. 同步 GitHub/Gitee master 并清理发布准备分支")
    print("  不会创建 tag、安装包或公开 Release")
    print(f"  精确授权: {expected_authorization(version)}")


def deliver_release_preparation(
    version: str,
    *,
    notes_file: Path | None = None,
    execute: bool = False,
    authorization: str = "",
    timeout: int = pr_delivery.DEFAULT_CHECK_TIMEOUT,
    poll_interval: int = pr_delivery.DEFAULT_POLL_INTERVAL,
) -> dict[str, Any]:
    """Preview or execute preparation and PR delivery as one transaction chain."""
    version = release_prepare.normalize_version(version)
    if execute:
        validate_authorization(version, authorization)

    branch = release_prepare.release_branch(version)
    if not execute:
        plan = release_prepare.inspect_repository(version)
        _print_preview(plan)
        return {"mode": "preview", "plan": plan, "branch": branch}

    print(f"\n>>> 阶段 1/2：准备 v{version} 发布材料")
    preparation = release_prepare.prepare_release(
        version,
        notes_file=notes_file,
        execute=True,
        authorization=release_prepare.expected_authorization(version),
        show_next_step=False,
    )
    if (
        preparation["mode"] == "already_merged"
        and not release_prepare._local_branch_exists(branch)
    ):
        print(f"\n[OK] v{version} 发布准备已经交付，无需重复创建 PR")
        return {
            "mode": "already_delivered",
            "preparation": preparation,
            "branch": branch,
        }

    if preparation["mode"] == "already_merged":
        print(f"\n>>> 阶段 2/2：收口已合并的 {branch}")
    else:
        print(f"\n>>> 阶段 2/2：交付 {branch}")
    delivery = pr_delivery.deliver(
        branch,
        execute=True,
        authorization=pr_delivery.expected_authorization(branch),
        title=f"chore: 准备 v{version} 正式发布",
        timeout=timeout,
        poll_interval=poll_interval,
    )
    print(f"\n[OK] v{version} 发布准备与分支交付全部完成")
    print(f"  下一步（单独授权）: 正式发布 v{version}")
    return {
        "mode": "delivered",
        "preparation": preparation,
        "delivery": delivery,
        "branch": branch,
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="版本准备与发布准备 PR 一键交付")
    parser.add_argument("--version", required=True, help="目标版本，不带 v 前缀")
    parser.add_argument("--notes-file", type=Path, help="经复核且位于项目外的 UTF-8 发布说明")
    parser.add_argument("--execute", action="store_true", help="执行准备、push、PR、合并和清理")
    parser.add_argument("--authorization", default="", help="精确授权文本")
    parser.add_argument("--timeout", type=int, default=pr_delivery.DEFAULT_CHECK_TIMEOUT)
    parser.add_argument("--poll-interval", type=int, default=pr_delivery.DEFAULT_POLL_INTERVAL)
    return parser


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    args = _build_parser().parse_args()
    try:
        deliver_release_preparation(
            args.version,
            notes_file=args.notes_file,
            execute=args.execute,
            authorization=args.authorization,
            timeout=args.timeout,
            poll_interval=args.poll_interval,
        )
    except (
        ReleaseDeliveryError,
        release_prepare.ReleasePreparationError,
        pr_delivery.PRDeliveryError,
        subprocess.CalledProcessError,
    ) as exc:
        print(f"\n[失败] {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
