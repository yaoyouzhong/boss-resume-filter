import importlib.util
import subprocess
import tempfile
from argparse import Namespace
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import call, patch


BASE_DIR = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "boss_resume_filter_release_prepare",
    BASE_DIR / "scripts" / "release_prepare.py",
)
assert SPEC and SPEC.loader
release_prepare = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(release_prepare)


VALID_NOTES = """## v2.22 — 候选人跟进闭环

### 新增功能

- **跟进日期管理**：支持安排候选人的下一次跟进日期

### 体验优化

- **待办展示优化**：按处理时限归组展示候选人
"""


@contextmanager
def _raises(error_type, message: str):
    try:
        yield
    except error_type as exc:
        assert message in str(exc)
    else:
        raise AssertionError(f"expected {error_type.__name__}: {message}")


def _plan(state: str = "new", *, dirty_paths=None, working_version: str = "2.21"):
    return {
        "version": "2.22",
        "branch": "master" if state != "resume" else "codex/release-v2.22",
        "release_branch": "codex/release-v2.22",
        "state": state,
        "head_sha": "a" * 40,
        "master_sha": "a" * 40,
        "base_version": "2.21",
        "working_version": working_version,
        "last_tag": "v2.21",
        "commits": [{"sha": "b" * 40, "subject": "feat: test"}],
        "changed_files": ["gui_main.py"],
        "dirty_paths": dirty_paths or [],
    }


def test_version_and_authorization_follow_project_contract():
    assert release_prepare.normalize_version("v2.22") == "2.22"
    assert release_prepare.normalize_version("2.21.1") == "2.21.1"
    with _raises(release_prepare.ReleasePreparationError, "禁止使用 X.Y.0"):
        release_prepare.normalize_version("2.22.0")
    release_prepare.validate_authorization("2.22", "一键准备版本 v2.22")
    with _raises(release_prepare.ReleasePreparationError, "授权不匹配"):
        release_prepare.validate_authorization("2.22", "继续")


def test_target_tag_must_not_exist_locally_or_on_either_remote():
    with (
        patch.object(release_prepare, "_local_tag_commit", return_value="a" * 40),
        patch.object(release_prepare, "_remote_tag_commit", return_value=""),
    ):
        with _raises(release_prepare.ReleasePreparationError, "已存在于本地"):
            release_prepare.assert_target_tag_available("2.22")

    with (
        patch.object(release_prepare, "_local_tag_commit", return_value=""),
        patch.object(
            release_prepare,
            "_remote_tag_commit",
            side_effect=["a" * 40, "b" * 40],
        ),
    ):
        with _raises(release_prepare.ReleasePreparationError, "GitHub/Gitee"):
            release_prepare.assert_target_tag_available("2.22")


def test_status_paths_preserves_the_first_porcelain_status_prefix():
    status = " M AGENTS.md\nM  scripts/release_prepare.py\n?? new-file.md\n"
    completed = subprocess.CompletedProcess(
        ["git", "status", "--porcelain"],
        0,
        status,
        "",
    )
    with patch.object(release_prepare, "_run", return_value=completed):
        paths = release_prepare._status_paths()

    assert paths == {
        "AGENTS.md",
        "scripts/release_prepare.py",
        "new-file.md",
    }


def test_strict_gate_reuses_tests_only_for_matching_product_fingerprint():
    with (
        patch.object(
            release_prepare,
            "product_code_fingerprint",
            return_value="fingerprint",
        ),
        patch.object(
            release_prepare,
            "_test_evidence_matches",
            return_value=True,
        ),
        patch.object(release_prepare.build, "_preflight_checks") as preflight,
        patch.object(release_prepare, "_run"),
        patch.object(release_prepare, "_write_test_evidence") as write_evidence,
    ):
        release_prepare._run_strict_gate()

    preflight.assert_called_once_with(
        require_clean=False,
        strict_changelog=True,
        run_tests=False,
    )
    write_evidence.assert_not_called()


def test_release_notes_require_ordered_project_categories_and_entry_format():
    title, body = release_prepare.parse_release_notes(VALID_NOTES, "2.22")
    assert title == "v2.22 — 候选人跟进闭环"
    assert "### 新增功能" in body

    reversed_notes = VALID_NOTES.replace("### 新增功能", "### 临时").replace(
        "### 体验优化", "### 新增功能"
    ).replace("### 临时", "### 体验优化")
    with _raises(release_prepare.ReleasePreparationError, "必须按"):
        release_prepare.parse_release_notes(reversed_notes, "2.22")

    with _raises(release_prepare.ReleasePreparationError, "条目必须使用"):
        release_prepare.parse_release_notes(
            VALID_NOTES.replace("- **跟进日期管理**：", "- 跟进日期管理："),
            "2.22",
        )


def test_release_notes_reject_function_unrelated_release_process_entries():
    notes = VALID_NOTES.replace(
        "- **待办展示优化**：按处理时限归组展示候选人",
        "- **版本交付保障**：完善构建发布和双远端验收流程",
    )
    with _raises(release_prepare.ReleasePreparationError, "功能无关的工程过程"):
        release_prepare.parse_release_notes(notes, "2.22")


def test_release_preview_prints_scope_and_completeness_checklist():
    with patch("builtins.print") as output:
        release_prepare._print_plan(_plan())

    text = "\n".join(str(item.args[0]) for item in output.call_args_list if item.args)
    assert "上一公开版本" in text
    assert "开发中引入" in text
    assert "确认用户变化已写入、合并表述或明确排除" in text


def test_changelog_replacement_is_idempotent_and_keeps_history():
    original = """# 更新日志

## v2.21 — 旧版本

### 问题修复

- **旧问题**：修复旧问题
"""
    title, body = release_prepare.parse_release_notes(VALID_NOTES, "2.22")
    first = release_prepare._replace_changelog(original, "2.22", title, body)
    second = release_prepare._replace_changelog(first, "2.22", title, body)
    assert first == second
    assert first.count("## v2.22") == 1
    assert first.index("## v2.22") < first.index("## v2.21")


def test_same_version_resume_does_not_call_non_idempotent_version_writer():
    with (
        patch.object(release_prepare.build, "_read_version", return_value="2.22"),
        patch.object(release_prepare.build, "_write_version") as write_version,
    ):
        release_prepare._write_version_if_needed("2.22")

    write_version.assert_not_called()

    with (
        patch.object(release_prepare.build, "_read_version", return_value="2.21"),
        patch.object(release_prepare.build, "_write_version") as write_version,
    ):
        release_prepare._write_version_if_needed("2.22")

    write_version.assert_called_once_with("2.22")


def test_readme_replacement_keeps_three_detailed_versions_and_collapses_history():
    readme = """# Project

> 当前发布版本：v2.21 旧标题（版本号 v2.21）

## ✨ 功能特性

### v2.21 标题 21

**新增功能**

- **功能 21**：说明

### v2.20 标题 20

**新增功能**

- **功能 20**：说明

### v2.19 标题 19

**问题修复**

- **修复 19**：说明

### v2.18 及更早版本

> 完整版本历史见 [CHANGELOG.md](CHANGELOG.md)

## 🚀 快速开始

├── gui_main.py            # 图形界面主程序（v2.21）
"""
    title, body = release_prepare.parse_release_notes(VALID_NOTES, "2.22")
    updated = release_prepare._replace_readme(readme, "2.22", title, body)
    assert "当前发布版本：v2.22 候选人跟进闭环（版本号 v2.22）" in updated
    assert "### v2.22 候选人跟进闭环" in updated
    assert "### v2.21 标题 21" in updated
    assert "### v2.20 标题 20" in updated
    assert "### v2.19 及更早版本" in updated
    assert "### v2.19 标题 19" not in updated
    assert "图形界面主程序（v2.22）" in updated


def test_readme_replacement_accepts_current_development_section_boundary():
    readme = """> 当前发布版本：v2.21 旧标题（版本号 v2.21）

## 最近版本

### v2.21 标题 21

- 说明

### v2.20 标题 20

- 说明

## 开发与验证

原有内容

├── gui_main.py            # 图形界面主程序（v2.21）
"""
    title, body = release_prepare.parse_release_notes(VALID_NOTES, "2.22")

    updated = release_prepare._replace_readme(readme, "2.22", title, body)

    assert "## 开发与验证\n\n原有内容" in updated
    assert "### v2.22 候选人跟进闭环" in updated


def test_execute_rejects_wrong_authorization_before_inspection():
    with patch.object(release_prepare, "inspect_repository") as inspect:
        with _raises(release_prepare.ReleasePreparationError, "授权不匹配"):
            release_prepare.prepare_release(
                "2.22",
                execute=True,
                authorization="正式发布 v2.22",
            )
    inspect.assert_not_called()


def test_main_switches_to_pack_venv_before_preparing_release():
    events = []
    args = Namespace(
        version="2.22",
        notes_file=None,
        execute=False,
        authorization="",
    )
    with (
        patch.object(
            release_prepare.build,
            "run_in_venv",
            side_effect=lambda *_: events.append("venv"),
        ) as run_in_venv,
        patch.object(release_prepare, "_build_parser") as build_parser,
        patch.object(
            release_prepare,
            "prepare_release",
            side_effect=lambda *_args, **_kwargs: events.append("prepare"),
        ),
    ):
        build_parser.return_value.parse_args.return_value = args
        assert release_prepare.main() == 0

    run_in_venv.assert_called_once_with(release_prepare.__file__)
    assert events == ["venv", "prepare"]


def test_release_notes_input_must_stay_outside_the_repository():
    with patch.object(release_prepare, "inspect_repository") as inspect:
        with _raises(release_prepare.ReleasePreparationError, "项目目录之外"):
            release_prepare.prepare_release(
                "2.22",
                notes_file=BASE_DIR / "release-notes-v2.22.md",
                execute=True,
                authorization="一键准备版本 v2.22",
            )
    inspect.assert_not_called()


def test_preview_only_inspects_and_reports_plan():
    plan = _plan()
    with (
        patch.object(release_prepare, "inspect_repository", return_value=plan),
        patch.object(release_prepare, "apply_release_materials") as apply_materials,
        patch.object(release_prepare, "_run") as run,
    ):
        result = release_prepare.prepare_release("2.22")

    assert result == {"mode": "preview", "plan": plan}
    apply_materials.assert_not_called()
    run.assert_not_called()


def test_execute_creates_local_branch_runs_gate_and_commits_without_push():
    plan = _plan()
    with tempfile.TemporaryDirectory() as temp_dir:
        notes = Path(temp_dir) / "notes.md"
        notes.write_text(VALID_NOTES, encoding="utf-8")
        run_calls = []

        def fake_run(args, **_kwargs):
            run_calls.append(args)
            return subprocess.CompletedProcess(args, 0, "", "")

        with (
            patch.object(release_prepare, "inspect_repository", return_value=plan),
            patch.object(release_prepare, "_local_branch_exists", return_value=False),
            patch.object(release_prepare, "apply_release_materials") as apply_materials,
            patch.object(release_prepare, "_status_paths", return_value=set(release_prepare.RELEASE_FILES)),
            patch.object(release_prepare, "_run_strict_gate") as gate,
            patch.object(release_prepare, "_run", side_effect=fake_run),
            patch.object(release_prepare, "_git_text", return_value="c" * 40),
        ):
            result = release_prepare.prepare_release(
                "2.22",
                notes_file=notes,
                execute=True,
                authorization="一键准备版本 v2.22",
            )

    assert result["mode"] == "prepared"
    apply_materials.assert_called_once()
    gate.assert_called_once_with()
    assert ["git", "switch", "-c", "codex/release-v2.22"] in run_calls
    assert ["git", "commit", "-m", "chore: 准备 v2.22 正式发布"] in run_calls
    assert not any(args[:2] == ["git", "push"] for args in run_calls)


def test_clean_prepared_branch_is_verified_without_requiring_notes_or_committing():
    plan = _plan(state="resume", working_version="2.22")
    with (
        patch.object(release_prepare, "inspect_repository", return_value=plan),
        patch.object(release_prepare, "_run_strict_gate") as gate,
        patch.object(release_prepare, "apply_release_materials") as apply_materials,
        patch.object(release_prepare, "_run") as run,
    ):
        result = release_prepare.prepare_release(
            "2.22",
            execute=True,
            authorization="一键准备版本 v2.22",
        )

    assert result["mode"] == "already_prepared"
    gate.assert_called_once_with()
    apply_materials.assert_not_called()
    run.assert_not_called()
