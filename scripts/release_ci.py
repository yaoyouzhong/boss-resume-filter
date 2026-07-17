"""Hosted, single-authorization release orchestration.

The GitHub Actions workflow calls this module in two phases:

``prepare``
    Validate the explicit release authorization, resolve the immutable release
    commit, run the strict project gate, and decide which platform artifacts
    still need building.

``publish``
    Create or reuse the immutable tag, stage and publish GitHub/Gitee releases,
    update ``latest.json`` on ``master``, mirror the final commit to Gitee, and
    perform the public post-release checks.

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
from pathlib import Path
from urllib.parse import quote


BASE_DIR = Path(__file__).resolve().parents[1]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

import build  # noqa: E402


RELEASE_ARTIFACTS = (
    "BOSS_ResumeFilter.exe",
    "BOSS_ResumeFilter_mac.zip",
    "BOSS_ResumeFilter.dmg",
)
RESUME_ONLY_MASTER_CHANGES = frozenset({"latest.json"})
GITEE_OWNER = "yaoyouzhong"
GITEE_REPO = "boss-resume-filter"


class ReleaseAutomationError(RuntimeError):
    """A deterministic release contract was not satisfied."""


def _fail(message: str) -> None:
    raise ReleaseAutomationError(message)


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


def _normalize_version(value: str) -> str:
    version = str(value or "").strip().removeprefix("v")
    build._validate_version_format(version)
    return version


def expected_authorization(version: str) -> str:
    return f"正式发布 v{version}"


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
    dry_run: bool = False,
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
    release_title, _release_notes = build._extract_changelog_release(version)

    # A local dry run may inspect an intentionally dirty implementation branch.
    # Hosted dry runs still start from a clean checkout, but do not need a
    # special code path.
    build._preflight_checks(
        require_clean=not dry_run,
        strict_changelog=True,
    )

    tag = f"v{version}"
    remote_assets = build._get_github_release_assets(tag) if resume else {}
    needs_windows = not _asset_has_integrity(remote_assets.get(RELEASE_ARTIFACTS[0]))
    needs_macos = not all(
        _asset_has_integrity(remote_assets.get(name))
        for name in RELEASE_ARTIFACTS[1:]
    )

    outputs = {
        "version": version,
        "tag": tag,
        "release_sha": release_sha,
        "resume": str(resume).lower(),
        "needs_windows": str(needs_windows).lower(),
        "needs_macos": str(needs_macos).lower(),
    }
    _write_github_outputs(github_output, outputs)

    mode = "断点续跑" if resume else "首次发布"
    print(f"\n[OK] {tag} 发布准备通过（{mode}）")
    print(f"  发布提交: {release_sha}")
    print(f"  Release 标题: {release_title}")
    print(f"  Windows 构建: {'需要' if needs_windows else '复用'}")
    print(f"  macOS 构建: {'需要' if needs_macos else '复用'}")
    if dry_run:
        print("  Dry Run：未创建标签、未上传附件、未发布")
    return outputs


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
        _run(["git", "tag", "-a", tag, release_sha, "-F", str(notes_path)])
        print(f"  [OK] 已创建 annotated tag: {tag}")

    if not remote_sha:
        _run(["git", "push", "origin", f"refs/tags/{tag}"])
        print(f"  [OK] 已推送 GitHub tag: {tag}")
    else:
        print(f"  [跳过] GitHub tag 已存在且提交一致: {tag}")


def _require_publish_secrets() -> str:
    if not (os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")):
        _fail("缺少 GH_TOKEN/GITHUB_TOKEN，无法发布 GitHub Release")
    token = os.environ.get("GITEE_TOKEN", "")
    if not token:
        _fail("缺少 GITEE_TOKEN，无法完成双远端发布")
    return token


def _sanitized_git_push(url: str, refspec: str, token: str) -> None:
    result = _run(
        ["git", "push", url, refspec],
        check=False,
        capture_output=True,
    )
    if result.returncode == 0:
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


def _ensure_gitee_tag(tag: str, release_sha: str, token: str) -> None:
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
            _run([
                "gh", "release", "create", tag,
                "--draft",
                "--verify-tag",
                "--title", title,
                "--notes-file", str(body_path),
            ])
            print(f"  [OK] 已创建 GitHub Draft Release: {tag}")
            return build._get_github_release_info(tag)

        if info.get("tagName") != tag:
            _fail("GitHub Release 的标签与请求不一致")
        _run([
            "gh", "release", "edit", tag,
            "--title", title,
            "--notes-file", str(body_path),
        ])
        print(f"  [跳过] GitHub Release 已存在，已同步标题和说明: {tag}")
        return build._get_github_release_info(tag)
    finally:
        notes_path.unlink(missing_ok=True)
        body_path.unlink(missing_ok=True)


def _ensure_local_artifacts(tag: str) -> list[Path]:
    build.DIST_DIR.mkdir(parents=True, exist_ok=True)
    remote_assets = build._get_github_release_assets(tag)
    paths: list[Path] = []
    for name in RELEASE_ARTIFACTS:
        path = build.DIST_DIR / name
        if not path.exists() and name in remote_assets:
            print(f"  [复用] 从现有 GitHub Release 下载: {name}")
            build._download_from_github_release(tag, name, build.DIST_DIR)
        if not path.exists() or path.stat().st_size <= 0:
            _fail(f"发布产物缺失或为空：{name}")
        paths.append(path)
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
    base = f"https://gitee.com/{GITEE_OWNER}/{GITEE_REPO}/releases/download/v{version}"
    return {
        "windows": f"{base}/BOSS_ResumeFilter.exe",
        "macos": f"{base}/BOSS_ResumeFilter_mac.zip",
        "macos_dmg": f"{base}/BOSS_ResumeFilter.dmg",
    }


def _publish_gitee_artifacts(
    version: str,
    title: str,
    body: str,
    artifacts: list[Path],
    github_assets: dict,
) -> dict[str, str]:
    cache = build._gitee_get_release_cache(version, title, body)
    if cache is None:
        _fail("无法创建或读取 Gitee Release")
    uploaded = build._gitee_upload_artifacts(
        version,
        title,
        body,
        artifacts,
        release_cache=cache,
        large_workers=1,
        fail_fast=True,
    )
    if not uploaded:
        _fail("Gitee Release 附件上传失败")
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
        _run(["gh", "release", "edit", tag, "--draft=false"])
        print(f"  [OK] GitHub Release 已正式发布: {tag}")
    else:
        print(f"  [跳过] GitHub Release 已是正式版本: {tag}")


def _fetch_and_assert_current_master_compatible(release_sha: str) -> str:
    """Re-read origin/master and reject an unsafe release/resume race."""
    _run(["git", "fetch", "origin", "master"])
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
    build._assert_update_asset_metadata_complete(metadata)
    changed = build.update_latest_json(
        version,
        body,
        downloads_cn,
        asset_metadata=metadata,
        require_complete_assets=True,
    )
    if changed:
        status_lines = _git_text("status", "--porcelain").splitlines()
        changed_paths = {
            line[3:].split(" -> ", 1)[-1].replace("\\", "/")
            for line in status_lines
            if line.strip()
        }
        if changed_paths != {"latest.json"}:
            _fail(
                "更新自动更新清单时出现非预期文件："
                + ", ".join(sorted(changed_paths))
            )
        _run(["git", "config", "user.name", "github-actions[bot]"])
        _run([
            "git", "config", "user.email",
            "41898282+github-actions[bot]@users.noreply.github.com",
        ])
        _run(["git", "add", "latest.json"])
        _run(["git", "commit", "-m", "chore: 更新自动更新清单"])
        _run(["git", "push", "origin", "master"])
        print("  [OK] latest.json 已提交并推送到 GitHub master")
    else:
        print("  [跳过] latest.json 已一致")

    current_master = _git_text("rev-parse", "HEAD")
    _sanitized_git_push(
        _gitee_authenticated_url(gitee_token),
        "HEAD:refs/heads/master",
        gitee_token,
    )
    if build._remote_ref_commit("gitee", "refs/heads/master") != current_master:
        _fail("Gitee master 推送后校验失败")
    print("  [OK] Gitee master 已同步")
    return current_master


def _request_ok(url: str) -> bool:
    headers = {"Range": "bytes=0-0"}
    try:
        response = build.requests.head(url, allow_redirects=True, timeout=30)
        if response.status_code < 400:
            return True
        response = build.requests.get(
            url,
            headers=headers,
            allow_redirects=True,
            stream=True,
            timeout=30,
        )
        try:
            return response.status_code < 400
        finally:
            response.close()
    except build.requests.exceptions.RequestException:
        return False


def verify_public_endpoints(version: str, attempts: int = 6, delay: int = 10) -> None:
    """Verify public downloads plus both remotely served manifests."""
    version = _normalize_version(version)
    latest = json.loads((BASE_DIR / "latest.json").read_text(encoding="utf-8"))
    urls = [
        *latest.get("downloads", {}).values(),
        *latest.get("downloads_cn", {}).values(),
    ]
    if len(urls) != 6:
        _fail("latest.json 未包含六个双源公开下载地址")

    manifest_urls = (
        f"https://raw.githubusercontent.com/{GITEE_OWNER}/{GITEE_REPO}/master/latest.json",
        f"https://gitee.com/{GITEE_OWNER}/{GITEE_REPO}/raw/master/latest.json",
    )
    last_errors: list[str] = []
    for attempt in range(1, attempts + 1):
        last_errors = [url for url in urls if not _request_ok(url)]
        for url in manifest_urls:
            try:
                response = build.requests.get(url, timeout=30)
                response.raise_for_status()
                remote = response.json()
                if remote.get("version") != version:
                    last_errors.append(f"{url} -> {remote.get('version')!r}")
            except (build.requests.exceptions.RequestException, ValueError) as exc:
                last_errors.append(f"{url} -> {type(exc).__name__}")
        if not last_errors:
            print("  [OK] 六个公开下载地址和双远端在线清单均可用")
            return
        if attempt < attempts:
            print(f"  [等待] 公开资源尚未全部生效（{attempt}/{attempts}）")
            time.sleep(delay)
    _fail("公开资源验收失败：" + "; ".join(last_errors))


def publish_release(
    version: str,
    authorization: str,
    release_sha: str,
) -> None:
    """Publish or resume one release from its immutable source commit."""
    version = _normalize_version(version)
    validate_authorization(version, authorization)
    gitee_token = _require_publish_secrets()
    _ensure_gitee_remote()
    release_sha = release_sha.strip()
    head_sha = _git_text("rev-parse", "HEAD")
    if head_sha != release_sha:
        _fail("publish job 未检出 prepare 阶段确定的发布提交")
    if _version_at_ref(release_sha) != version:
        _fail("发布提交版本与请求版本不一致")

    # Build jobs can run for tens of minutes.  Recheck master before the first
    # remote mutation, then once more immediately before making GitHub public.
    _fetch_and_assert_current_master_compatible(release_sha)

    tag = f"v{version}"
    title, body = build._extract_changelog_release(version)
    tag_notes = _notes_file(title, body)
    try:
        _ensure_origin_tag(tag, release_sha, tag_notes)
    finally:
        tag_notes.unlink(missing_ok=True)
    _ensure_gitee_tag(tag, release_sha, gitee_token)

    _ensure_github_release(tag, title, body)
    artifacts = _ensure_local_artifacts(tag)
    github_assets = _upload_github_artifacts(tag, artifacts)
    downloads_cn = _publish_gitee_artifacts(
        version, title, body, artifacts, github_assets
    )

    # Publicize only after both release stores have complete artifacts.
    _fetch_and_assert_current_master_compatible(release_sha)
    _publish_github_release(tag)
    _commit_and_sync_manifest(
        version,
        body,
        downloads_cn,
        github_assets,
        release_sha,
        gitee_token,
    )

    if not build._verify_release_remote_state(version):
        _fail("双远端发布状态核验失败")
    verify_public_endpoints(version)
    print(f"\n[OK] {tag} 正式发布、自动更新清单和线上验收全部完成")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="GitHub Actions 正式发布编排")
    subparsers = parser.add_subparsers(dest="command", required=True)

    prepare = subparsers.add_parser("prepare", help="运行无副作用严格门禁")
    prepare.add_argument("--version", required=True)
    prepare.add_argument("--authorization", required=True)
    prepare.add_argument("--github-output")
    prepare.add_argument("--dry-run", action="store_true")

    publish = subparsers.add_parser("publish", help="发布或断点续跑")
    publish.add_argument("--version", required=True)
    publish.add_argument("--authorization", required=True)
    publish.add_argument("--release-sha", required=True)

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
                dry_run=args.dry_run or env_dry_run,
                github_output=args.github_output,
            )
        elif args.command == "publish":
            publish_release(args.version, args.authorization, args.release_sha)
        else:
            verify_public_endpoints(args.version)
    except ReleaseAutomationError as exc:
        print(f"[错误] {exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
