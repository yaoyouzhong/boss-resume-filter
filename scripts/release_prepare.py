"""Deterministic local preparation for one formal release.

The default mode is mutation-free and reports the exact changes since the
latest public version.  Execution requires an explicit release-notes file and
the exact authorization text ``一键准备版本 vX.Y``.  It creates a local
``codex/release-vX.Y`` branch, synchronizes the project's version documents,
runs the strict release gate, and creates one local commit.

This module never pushes, opens or merges a pull request, creates a tag, or
starts the formal release workflow.
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path
from typing import Any


BASE_DIR = Path(__file__).resolve().parents[1]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

import build  # noqa: E402


RELEASE_FILES = frozenset({
    "AGENTS.md",
    "CHANGELOG.md",
    "CLAUDE.md",
    "README.md",
    "gui_main.py",
})
RELEASE_CATEGORIES = ("新增功能", "体验优化", "问题修复")
VERSION_PATTERN = re.compile(r"^\d+\.\d+(?:\.\d+)?$")
VERSION_ASSIGNMENT_PATTERN = re.compile(
    r'^__version__\s*=\s*["\']([^"\']+)["\']',
    re.MULTILINE,
)


class ReleasePreparationError(RuntimeError):
    """A deterministic release-preparation contract was not satisfied."""


def _fail(message: str) -> None:
    raise ReleasePreparationError(message)


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


def normalize_version(value: str) -> str:
    """Normalize and validate the repository's X.Y / X.Y.Z version format."""
    version = str(value or "").strip().removeprefix("v")
    if not VERSION_PATTERN.fullmatch(version):
        _fail("版本号必须使用 X.Y 或 X.Y.Z 格式")
    parts = version.split(".")
    if len(parts) == 3 and parts[2] == "0":
        _fail("禁止使用 X.Y.0；大版本必须写为 X.Y")
    return version


def _version_key(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in version.split("."))


def expected_authorization(version: str) -> str:
    return f"一键准备版本 v{version}"


def validate_authorization(version: str, authorization: str) -> None:
    expected = expected_authorization(version)
    if authorization != expected:
        _fail(f"发布准备授权不匹配：必须准确填写 {expected!r}")


def release_branch(version: str) -> str:
    return f"codex/release-v{version}"


def _version_at_ref(ref: str) -> str:
    source = _git_text("show", f"{ref}:gui_main.py")
    match = VERSION_ASSIGNMENT_PATTERN.search(source)
    if not match:
        _fail(f"无法从 {ref} 的 gui_main.py 读取版本号")
    return normalize_version(match.group(1))


def _is_ancestor(ancestor: str, descendant: str) -> bool:
    result = _run(
        ["git", "merge-base", "--is-ancestor", ancestor, descendant],
        check=False,
        capture_output=True,
    )
    return result.returncode == 0


def _status_paths() -> set[str]:
    output = _git_text("status", "--porcelain")
    paths: set[str] = set()
    for line in output.splitlines():
        if len(line) < 4:
            _fail(f"无法解析 git status 输出：{line!r}")
        paths.add(line[3:].split(" -> ", 1)[-1].replace("\\", "/"))
    return paths


def _local_branch_exists(branch: str) -> bool:
    result = _run(
        ["git", "show-ref", "--verify", "--quiet", f"refs/heads/{branch}"],
        check=False,
    )
    return result.returncode == 0


def _remote_tag_commit(remote: str, tag: str) -> str:
    output = _git_text("ls-remote", remote, f"refs/tags/{tag}^{{}}")
    if not output:
        output = _git_text("ls-remote", remote, f"refs/tags/{tag}")
    return output.split()[0] if output else ""


def _local_tag_commit(tag: str) -> str:
    result = _run(
        ["git", "rev-parse", "--verify", f"{tag}^{{commit}}"],
        check=False,
        capture_output=True,
    )
    return result.stdout.strip() if result.returncode == 0 else ""


def assert_target_tag_available(version: str) -> None:
    tag = f"v{version}"
    locations = []
    if _local_tag_commit(tag):
        locations.append("本地")
    if _remote_tag_commit("origin", tag):
        locations.append("GitHub")
    if _remote_tag_commit("gitee", tag):
        locations.append("Gitee")
    if locations:
        _fail(f"{tag} 已存在于{'/'.join(locations)}，不能再次准备")


def _latest_public_tag(ref: str, source_version: str) -> str:
    preferred = f"v{source_version}"
    if _run(
        ["git", "merge-base", "--is-ancestor", preferred, ref],
        check=False,
        capture_output=True,
    ).returncode == 0:
        return preferred

    tags = _git_text("tag", "--merged", ref, "--sort=-version:refname")
    for tag in tags.splitlines():
        if re.fullmatch(r"v\d+\.\d+(?:\.\d+)?", tag):
            return tag
    _fail("无法找到可用于生成发布范围的公开版本标签")


def inspect_repository(version: str) -> dict[str, Any]:
    """Inspect the safe preparation state without changing tracked files."""
    version = normalize_version(version)
    branch = _git_text("branch", "--show-current")
    expected_branch = release_branch(version)
    if branch not in {"master", expected_branch}:
        _fail(f"发布准备只能从 master 或 {expected_branch} 执行")

    paths = _status_paths()
    if branch == "master" and paths:
        _fail("master 工作区存在未提交修改，拒绝准备版本")
    unexpected = paths - RELEASE_FILES
    if unexpected:
        _fail("发布准备分支包含非发布材料修改：" + ", ".join(sorted(unexpected)))

    _run(["git", "fetch", "origin"])
    _run(["git", "fetch", "gitee"])
    head_sha = _git_text("rev-parse", "HEAD")
    origin_master = _git_text("rev-parse", "origin/master")
    gitee_master = _git_text("rev-parse", "gitee/master")
    if origin_master != gitee_master:
        _fail("GitHub/Gitee master 不一致，拒绝准备版本")
    if branch == "master" and head_sha != origin_master:
        _fail("本地 master 不是最新 origin/master")
    if branch == expected_branch and not _is_ancestor(origin_master, head_sha):
        _fail(f"{expected_branch} 未基于最新 origin/master")

    assert_target_tag_available(version)

    base_version = _version_at_ref("origin/master")
    working_version = normalize_version(build._read_version())
    if _version_key(version) < _version_key(base_version):
        _fail(f"目标版本 v{version} 低于 master 当前版本 v{base_version}")
    if branch == expected_branch and working_version not in {base_version, version}:
        _fail(f"发布准备分支源码版本异常：v{working_version}")

    last_tag = _latest_public_tag("origin/master", base_version)
    commit_text = _git_text(
        "log",
        f"{last_tag}..origin/master",
        "--pretty=format:%H%x09%s",
    )
    commits = []
    for line in commit_text.splitlines():
        sha, _, subject = line.partition("\t")
        if sha:
            commits.append({"sha": sha, "subject": subject})
    changed_text = _git_text("diff", "--name-only", f"{last_tag}..origin/master")
    changed_files = [line for line in changed_text.splitlines() if line]
    if base_version != version and not (set(changed_files) - {"latest.json"}):
        _fail(f"{last_tag} 之后没有可发布的业务或文档变更")

    state = "new"
    if base_version == version:
        state = "merged"
    elif branch == expected_branch:
        state = "resume"
    return {
        "version": version,
        "branch": branch,
        "release_branch": expected_branch,
        "state": state,
        "head_sha": head_sha,
        "master_sha": origin_master,
        "base_version": base_version,
        "working_version": working_version,
        "last_tag": last_tag,
        "commits": commits,
        "changed_files": changed_files,
        "dirty_paths": sorted(paths),
    }


def parse_release_notes(text: str, version: str) -> tuple[str, str]:
    """Extract and validate one project-formatted CHANGELOG section."""
    normalized = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    pattern = re.compile(
        rf"^##\s+(v{re.escape(version)}\s+—\s+[^\n]+)\n(?P<body>.*?)(?=^##\s+v|\Z)",
        re.MULTILINE | re.DOTALL,
    )
    match = pattern.search(normalized)
    if not match:
        _fail(f"发布说明文件缺少 '## v{version} — 标题' 段落")
    title = match.group(1).strip()
    body = match.group("body").strip()
    headings = re.findall(r"^###\s+(.+?)\s*$", body, re.MULTILINE)
    unknown = [heading for heading in headings if heading not in RELEASE_CATEGORIES]
    if unknown:
        _fail("发布说明包含未知分类：" + ", ".join(unknown))
    present = [category for category in RELEASE_CATEGORIES if category in headings]
    if not present:
        _fail("发布说明至少需要一个标准分类")
    if headings != present:
        _fail("发布说明分类必须按新增功能、体验优化、问题修复排列且不能重复")

    for index, category in enumerate(headings):
        start = body.index(f"### {category}") + len(f"### {category}")
        end = len(body)
        if index + 1 < len(headings):
            end = body.index(f"### {headings[index + 1]}", start)
        entries = [line for line in body[start:end].splitlines() if line.startswith("- ")]
        if not entries:
            _fail(f"发布说明分类“{category}”没有条目")
        invalid = [
            entry for entry in entries
            if not re.match(r"^-\s+\*\*[^*]+\*\*：[^\n]+$", entry)
        ]
        if invalid:
            _fail(f"“{category}”条目必须使用 '- **标题**：说明' 格式")
    return title, body


def _replace_changelog(content: str, version: str, title: str, body: str) -> str:
    section_pattern = re.compile(
        rf"^##\s+v{re.escape(version)}[^\n]*\n.*?(?=^##\s+v|\Z)",
        re.MULTILINE | re.DOTALL,
    )
    content = section_pattern.sub("", content).rstrip() + "\n"
    heading = "# 更新日志"
    if not content.startswith(heading):
        _fail("CHANGELOG.md 缺少顶层“更新日志”标题")
    remainder = content[len(heading):].lstrip("\n")
    return f"{heading}\n\n## {title}\n\n{body}\n\n{remainder}".rstrip() + "\n"


def _readme_summary(version: str, title: str, body: str) -> str:
    description = title.split("—", 1)[1].strip()
    summary_body = re.sub(
        r"^###\s+(新增功能|体验优化|问题修复)\s*$",
        lambda match: f"**{match.group(1)}**",
        body,
        flags=re.MULTILINE,
    )
    return f"### v{version} {description}\n\n{summary_body.strip()}\n"


def _readme_release_blocks(region: str) -> list[dict[str, Any]]:
    matches = list(re.finditer(r"^###\s+v(\d+\.\d+(?:\.\d+)?)([^\n]*)$", region, re.MULTILINE))
    blocks: list[dict[str, Any]] = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(region)
        blocks.append({
            "version": match.group(1),
            "collapsed": "及更早版本" in match.group(2),
            "text": region[match.start():end].strip(),
        })
    return blocks


def _replace_readme(content: str, version: str, title: str, body: str) -> str:
    description = title.split("—", 1)[1].strip()
    version_line = f"> 当前发布版本：v{version} {description}（版本号 v{version}）"
    content, count = re.subn(
        r"^>\s*当前发布版本：.*$",
        version_line,
        content,
        count=1,
        flags=re.MULTILINE,
    )
    if count != 1:
        _fail("README.md 无法定位当前发布版本标识")

    start_match = re.search(r"^###\s+v\d+\.\d+", content, re.MULTILINE)
    end_match = re.search(r"^##\s+🚀", content, re.MULTILINE)
    if not start_match or not end_match or end_match.start() <= start_match.start():
        _fail("README.md 无法定位版本摘要区域")
    region = content[start_match.start():end_match.start()]
    blocks = _readme_release_blocks(region)
    detailed = [block for block in blocks if not block["collapsed"] and block["version"] != version]
    kept = detailed[:2]
    remaining = detailed[2:]
    collapsed = next((block for block in blocks if block["collapsed"]), None)
    collapse_version = remaining[0]["version"] if remaining else (
        collapsed["version"] if collapsed else ""
    )

    parts = [_readme_summary(version, title, body).strip()]
    parts.extend(block["text"] for block in kept)
    if collapse_version:
        parts.append(
            f"### v{collapse_version} 及更早版本\n\n"
            "> 完整版本历史见 [CHANGELOG.md](CHANGELOG.md)"
        )
    new_region = "\n\n".join(parts).rstrip() + "\n\n"
    content = content[:start_match.start()] + new_region + content[end_match.start():]
    content, count = re.subn(
        r"(gui_main\.py\s+#\s+图形界面主程序（)v[^）]+(）)",
        rf"\g<1>v{version}\g<2>",
        content,
    )
    if count < 1:
        _fail("README.md 无法定位 gui_main.py 版本注释")
    return content


def _replace_project_doc_version(content: str, version: str, name: str) -> str:
    updated, count = re.subn(
        r"(gui_main\.py\s+#\s+图形界面主程序（)v[^）]+(）)",
        rf"\g<1>v{version}\g<2>",
        content,
    )
    if count != 1:
        _fail(f"{name} 无法唯一定位 gui_main.py 版本注释")
    return updated


def apply_release_materials(version: str, title: str, body: str) -> None:
    """Synchronize all tracked version and release-note sources."""
    build._write_version(version)
    changelog = BASE_DIR / "CHANGELOG.md"
    changelog.write_text(
        _replace_changelog(changelog.read_text(encoding="utf-8"), version, title, body),
        encoding="utf-8",
    )
    readme = BASE_DIR / "README.md"
    readme.write_text(
        _replace_readme(readme.read_text(encoding="utf-8"), version, title, body),
        encoding="utf-8",
    )
    for name in ("CLAUDE.md", "AGENTS.md"):
        path = BASE_DIR / name
        path.write_text(
            _replace_project_doc_version(path.read_text(encoding="utf-8"), version, name),
            encoding="utf-8",
        )


def _run_strict_gate() -> None:
    try:
        build._preflight_checks(require_clean=False, strict_changelog=True)
    except SystemExit as exc:
        _fail(f"严格发布门禁未通过（退出码 {exc.code}）")
    _run(["git", "diff", "--check"])


def _print_plan(plan: dict[str, Any]) -> None:
    print(f"\n>>> v{plan['version']} 发布准备预览")
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


def prepare_release(
    version: str,
    *,
    notes_file: Path | None = None,
    execute: bool = False,
    authorization: str = "",
) -> dict[str, Any]:
    """Preview or execute one local release-preparation transaction."""
    version = normalize_version(version)
    if execute:
        validate_authorization(version, authorization)
    notes_path: Path | None = None
    if notes_file is not None:
        notes_path = notes_file if notes_file.is_absolute() else BASE_DIR / notes_file
        notes_path = notes_path.resolve()
        if notes_path == BASE_DIR or BASE_DIR in notes_path.parents:
            _fail("发布说明是临时输入文件，必须放在项目目录之外")
    plan = inspect_repository(version)
    _print_plan(plan)

    if not execute:
        print("\n未修改任何文件。执行前请准备经过复核的 UTF-8 发布说明文件。")
        print(f"精确授权：{expected_authorization(version)}")
        return {"mode": "preview", "plan": plan}

    if plan["state"] == "merged":
        _run_strict_gate()
        print(f"\n[OK] v{version} 发布准备已经合并到 master")
        return {"mode": "already_merged", "plan": plan}

    if plan["state"] == "resume" and not plan["dirty_paths"] and plan["working_version"] == version:
        _run_strict_gate()
        print(f"\n[OK] {plan['release_branch']} 已准备完成，无需重复提交")
        return {"mode": "already_prepared", "plan": plan}

    if notes_path is None:
        _fail("执行发布准备必须提供 --notes-file")
    if not notes_path.is_file():
        _fail(f"发布说明文件不存在：{notes_path}")
    title, body = parse_release_notes(notes_path.read_text(encoding="utf-8"), version)

    if plan["state"] == "new":
        if _local_branch_exists(plan["release_branch"]):
            _fail(f"本地分支 {plan['release_branch']} 已存在，请先检查其状态")
        _run(["git", "switch", "-c", plan["release_branch"]])

    apply_release_materials(version, title, body)
    changed_paths = _status_paths()
    unexpected = changed_paths - RELEASE_FILES
    if unexpected:
        _fail("严格门禁前发现意外修改：" + ", ".join(sorted(unexpected)))
    if not changed_paths:
        _fail("发布准备没有产生任何文件变更")
    _run_strict_gate()
    _run(["git", "add", *sorted(RELEASE_FILES)])
    _run(["git", "diff", "--cached", "--check"])
    _run(["git", "commit", "-m", f"chore: 准备 v{version} 正式发布"])
    commit_sha = _git_text("rev-parse", "HEAD")
    print(f"\n[OK] v{version} 发布准备已完成")
    print(f"  分支: {plan['release_branch']}")
    print(f"  提交: {commit_sha}")
    print(f"  下一步: 一键交付分支 {plan['release_branch']}")
    return {
        "mode": "prepared",
        "plan": plan,
        "branch": plan["release_branch"],
        "commit_sha": commit_sha,
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="本地版本发布准备自动化")
    parser.add_argument("--version", required=True, help="目标版本，不带 v 前缀")
    parser.add_argument("--notes-file", type=Path, help="经复核的 UTF-8 发布说明")
    parser.add_argument("--execute", action="store_true", help="执行本地分支、文档和提交变更")
    parser.add_argument("--authorization", default="", help="精确授权文本")
    return parser


def main() -> int:
    args = _build_parser().parse_args()
    try:
        prepare_release(
            args.version,
            notes_file=args.notes_file,
            execute=args.execute,
            authorization=args.authorization,
        )
    except (ReleasePreparationError, subprocess.CalledProcessError) as exc:
        print(f"[错误] {exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
