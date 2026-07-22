import importlib.util
import subprocess
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import call, patch


BASE_DIR = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "boss_resume_filter_release_flow",
    BASE_DIR / "scripts" / "release_flow.py",
)
assert SPEC and SPEC.loader
release_flow = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(release_flow)


@contextmanager
def _raises(error_type, message: str):
    try:
        yield
    except error_type as exc:
        assert message in str(exc)
    else:
        raise AssertionError(f"expected {error_type.__name__}: {message}")


def _candidate_state():
    return {
        "schema": 1,
        "phase": "awaiting_content_approval",
        "version": "2.24",
        "source_branches": ["codex/feature-a"],
        "tested_branches": {},
        "candidate_branch": "codex/feature-a",
        "candidate_sha": "a" * 40,
        "candidate_tree_sha": "t" * 40,
        "base_sha": "b" * 40,
        "pr_number": 42,
        "pr_url": "https://github.example/pr/42",
        "release_title": "v2.24 — Test",
        "release_body": "### 问题修复\n\n- **Test**：Description",
        "content_sha": "c" * 64,
    }


def test_authorizations_are_exact_and_multi_branch_order_is_explicit():
    assert release_flow.expected_start_authorization("2.24", ["codex/a"]) == (
        "一键发布版本 v2.24"
    )
    assert release_flow.expected_start_authorization(
        "2.24", ["codex/a", "codex/b"]
    ) == "一键发布版本 v2.24，包含 codex/a、codex/b"
    assert release_flow.expected_confirm_authorization("2.24") == "确认发布 v2.24"


def test_apply_release_materials_reuses_commit_when_staging_has_no_diff(tmp_path):
    notes = tmp_path / "notes.md"
    notes.write_text("release notes", encoding="utf-8")
    commands = []

    def run(args, **kwargs):
        commands.append(args)
        return subprocess.CompletedProcess(args, 0, "", "")

    with (
        patch.object(
            release_flow.release_prepare,
            "parse_release_notes",
            return_value=("v2.24 — Test", "### 问题修复\n\n- Test"),
        ),
        patch.object(release_flow.release_prepare, "apply_release_materials"),
        patch.object(
            release_flow.release_prepare,
            "_status_paths",
            return_value={"CHANGELOG.md"},
        ),
        patch.object(release_flow.release_prepare, "_run_strict_gate"),
        patch.object(release_flow, "_run", side_effect=run),
    ):
        release_flow._apply_release_materials("2.24", notes)

    assert ["git", "diff", "--cached", "--quiet"] in commands
    assert not any(command[:2] == ["git", "commit"] for command in commands)


def test_multi_branch_gui_evidence_must_match_each_exact_head():
    branches = ["codex/a", "codex/b"]
    tested = {"codex/a": "a" * 40, "codex/b": "b" * 40}

    def git_text(*args):
        if args == ("rev-parse", "codex/a"):
            return "a" * 40
        if args == ("rev-parse", "codex/b"):
            return "x" * 40
        raise AssertionError(args)

    with (
        patch.object(release_flow.pr_delivery, "_local_branch_exists", return_value=True),
        patch.object(release_flow.pr_delivery, "_worktree_for_branch", return_value=Path("D:/branch")),
        patch.object(release_flow.pr_delivery, "_assert_clean_worktree"),
        patch.object(release_flow, "_run"),
        patch.object(release_flow, "_git_text", side_effect=git_text),
        _raises(release_flow.ReleaseFlowError, "GUI 实测凭证"),
    ):
        release_flow._validate_source_branches(branches, tested)


def test_multi_branch_tests_run_inside_each_branch_worktree():
    branch = "codex/a"
    head = "a" * 40
    worktree = Path("D:/worktrees/a")
    with (
        patch.object(release_flow.pr_delivery, "_local_branch_exists", return_value=True),
        patch.object(release_flow.pr_delivery, "_worktree_for_branch", return_value=worktree),
        patch.object(release_flow.pr_delivery, "_assert_clean_worktree") as clean,
        patch.object(release_flow, "_git_text", return_value=head),
        patch.object(release_flow, "_run") as run,
    ):
        release_flow._validate_source_branches([branch], {branch: head})

    assert run.call_args_list == [
        call([release_flow.sys.executable, "tests/run_unit_tests.py"], cwd=worktree),
        call([release_flow.sys.executable, "tests/test_import.py"], cwd=worktree),
    ]
    assert clean.call_count == 2


def test_existing_aggregate_must_contain_every_declared_source_branch():
    branches = ["codex/a", "codex/b"]
    tested = {"codex/a": "a" * 40, "codex/b": "b" * 40}
    with (
        patch.object(release_flow, "_validate_source_branches"),
        patch.object(release_flow.pr_delivery, "_local_branch_exists", return_value=True),
        patch.object(release_flow, "_git_text", return_value="codex/release-v2.24"),
        patch.object(
            release_flow.pr_delivery,
            "_is_ancestor",
            side_effect=[True, False],
        ),
        _raises(release_flow.ReleaseFlowError, "未完整包含"),
    ):
        release_flow._prepare_aggregate_branch(
            "2.24", branches, tested, "m" * 40,
        )


def test_prepare_candidate_stops_after_pr_checks_and_writes_review_state():
    gate = {"head_sha": "a" * 40, "master_sha": "b" * 40}
    pr = {"number": 42, "url": "https://github.example/pr/42"}
    checked = {**pr, "state": "OPEN"}
    review = {
        "release_title": "v2.24 — Test",
        "release_body": "### 问题修复\n\n- **Test**：Description",
        "candidate_sha": "a" * 40,
        "candidate_tree_sha": "t" * 40,
        "content_sha": "c" * 64,
        "review_warnings": [],
    }
    events = []
    with (
        patch.object(release_flow, "_git_text", return_value="codex/feature-a"),
        patch.object(release_flow, "_validate_notes_file", return_value=Path("D:/notes.md")),
        patch.object(release_flow, "_assert_clean"),
        patch.object(release_flow, "_fetch_and_verify_masters", return_value="b" * 40),
        patch.object(release_flow.release_prepare, "assert_target_tag_available"),
        patch.object(
            release_flow,
            "_prepare_single_branch",
            side_effect=lambda *_: events.append("branch") or "codex/feature-a",
        ),
        patch.object(
            release_flow,
            "_apply_release_materials",
            side_effect=lambda *_: events.append("materials"),
        ),
        patch.object(
            release_flow.pr_delivery,
            "preflight",
            side_effect=lambda *_args, **_kwargs: events.append("gate") or gate,
        ),
        patch.object(
            release_flow.pr_delivery,
            "_push_and_create_pr",
            side_effect=lambda *_args, **_kwargs: events.append("push_pr") or pr,
        ),
        patch.object(
            release_flow.pr_delivery,
            "wait_for_pr_checks",
            side_effect=lambda *_args, **_kwargs: events.append("checks") or checked,
        ),
        patch.object(release_flow, "_tree_sha", return_value="t" * 40),
        patch.object(
            release_flow.release_content_review,
            "review_release_candidate",
            return_value=review,
        ),
        patch.object(
            release_flow,
            "_write_state",
            side_effect=lambda *_: events.append("state"),
        ) as write_state,
        patch.object(release_flow, "_print_candidate"),
        patch.object(release_flow.pr_delivery, "_merge_pr") as merge,
        patch.object(release_flow, "_dispatch_formal_release") as dispatch,
    ):
        result = release_flow.prepare_candidate(
            "2.24",
            notes_file=Path("D:/notes.md"),
            branches=[],
            tested_branches=[],
            authorization="一键发布版本 v2.24",
            timeout=30,
            poll_interval=1,
        )

    assert events == ["branch", "materials", "gate", "push_pr", "checks", "state"]
    assert result["phase"] == "awaiting_content_approval"
    write_state.assert_called_once()
    merge.assert_not_called()
    dispatch.assert_not_called()


def test_confirm_rejects_changed_candidate_before_merge():
    state = _candidate_state()
    with (
        patch.object(release_flow, "_read_state", return_value=state),
        patch.object(
            release_flow,
            "_git_text",
            side_effect=["codex/feature-a", "d" * 40],
        ),
        patch.object(release_flow, "_assert_clean"),
        patch.object(release_flow.pr_delivery, "_merge_pr") as merge,
        _raises(release_flow.ReleaseFlowError, "提交在确认前发生变化"),
    ):
        release_flow.confirm_release(
            "2.24", "确认发布 v2.24", approved_content_sha="c" * 64,
        )
    merge.assert_not_called()


def test_confirm_rejects_stale_preview_digest_before_candidate_checks():
    state = _candidate_state()
    with (
        patch.object(release_flow, "_read_state", return_value=state),
        patch.object(release_flow, "_verify_candidate") as verify,
        _raises(release_flow.release_content_review.ReleaseContentReviewError, "必须重新展示并确认"),
    ):
        release_flow.confirm_release(
            "2.24", "确认发布 v2.24", approved_content_sha="d" * 64,
        )
    verify.assert_not_called()


def test_confirm_verifies_tree_then_merges_syncs_and_publishes():
    state = _candidate_state()
    pr = {
        "number": 42,
        "state": "OPEN",
        "headRefOid": "a" * 40,
        "baseRefOid": "b" * 40,
    }
    merged = {"mergeCommit": {"oid": "m" * 40}}
    events = []
    with (
        patch.object(release_flow, "_read_state", return_value=state),
        patch.object(release_flow, "_verify_candidate", return_value=pr),
        patch.object(
            release_flow.pr_delivery,
            "_merge_pr",
            side_effect=lambda *_: events.append("merge") or merged,
        ),
        patch.object(
            release_flow.pr_delivery,
            "_run_external",
            side_effect=lambda *_args, **_kwargs: events.append("fetch"),
        ),
        patch.object(release_flow, "_tree_sha", return_value="t" * 40),
        patch.object(
            release_flow.pr_delivery,
            "finalize_delivery",
            side_effect=lambda *_: events.append("sync"),
        ),
        patch.object(
            release_flow,
            "_write_state",
            side_effect=lambda *_: events.append("state"),
        ),
        patch.object(
            release_flow.release_content_review,
            "review_release_content",
            return_value={"content_sha": "f" * 64},
        ),
        patch.object(
            release_flow,
            "_dispatch_formal_release",
            side_effect=lambda *_: events.append("publish") or {"mode": "published"},
        ),
    ):
        result = release_flow.confirm_release(
            "2.24", "确认发布 v2.24", approved_content_sha="c" * 64,
        )

    assert events == ["merge", "fetch", "state", "sync", "state", "publish", "state"]
    assert result["phase"] == "complete"


def test_confirm_stops_if_squash_tree_differs_from_approved_candidate():
    state = _candidate_state()
    pr = {"number": 42, "state": "OPEN"}
    merged = {"mergeCommit": {"oid": "m" * 40}}
    with (
        patch.object(release_flow, "_read_state", return_value=state),
        patch.object(release_flow, "_verify_candidate", return_value=pr),
        patch.object(release_flow.pr_delivery, "_merge_pr", return_value=merged),
        patch.object(release_flow.pr_delivery, "_run_external"),
        patch.object(release_flow, "_tree_sha", return_value="x" * 40),
        patch.object(release_flow.pr_delivery, "finalize_delivery") as finalize,
        patch.object(release_flow, "_dispatch_formal_release") as dispatch,
        _raises(release_flow.ReleaseFlowError, "文件树与已确认候选不一致"),
    ):
        release_flow.confirm_release(
            "2.24", "确认发布 v2.24", approved_content_sha="c" * 64,
        )

    finalize.assert_not_called()
    dispatch.assert_not_called()


def test_confirm_resumes_sync_after_merge_without_remerging_pr():
    state = {
        **_candidate_state(),
        "phase": "merged_pending_sync",
        "merge_sha": "m" * 40,
    }
    with (
        patch.object(release_flow, "_read_state", return_value=state),
        patch.object(release_flow.pr_delivery, "_merge_pr") as merge,
        patch.object(release_flow.pr_delivery, "finalize_delivery") as finalize,
        patch.object(release_flow, "_write_state"),
        patch.object(
            release_flow.release_content_review,
            "review_release_content",
            return_value={"content_sha": "f" * 64},
        ),
        patch.object(
            release_flow,
            "_dispatch_formal_release",
            return_value={"mode": "published"},
        ),
    ):
        release_flow.confirm_release(
            "2.24", "确认发布 v2.24", approved_content_sha="c" * 64,
        )

    merge.assert_not_called()
    finalize.assert_called_once_with("codex/feature-a", "m" * 40)


def test_formal_release_uses_master_worktree_when_current_is_detached():
    master = Path("D:/master")
    completed = subprocess.CompletedProcess([], 0, "", "")
    with (
        patch.object(release_flow, "_git_text", return_value=""),
        patch.object(release_flow.pr_delivery, "_worktree_for_branch", return_value=master),
        patch.object(release_flow.subprocess, "run", return_value=completed) as run,
    ):
        result = release_flow._dispatch_formal_release("2.24", "c" * 64)

    assert result["mode"] == "published_from_master_worktree"
    assert run.call_args.kwargs["cwd"] == master
