import importlib.util
import subprocess
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import Mock, call, patch


BASE_DIR = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "boss_resume_filter_pr_delivery",
    BASE_DIR / "scripts" / "pr_delivery.py",
)
assert SPEC and SPEC.loader
pr_delivery = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(pr_delivery)


def _completed(stdout: str = "", returncode: int = 0):
    return subprocess.CompletedProcess([], returncode, stdout, "")


@contextmanager
def _raises(error_type, message: str):
    try:
        yield
    except error_type as exc:
        assert message in str(exc)
    else:
        raise AssertionError(f"expected {error_type.__name__}: {message}")


def test_delivery_authorization_must_match_branch_exactly():
    branch = "codex/pr-delivery"
    pr_delivery.validate_authorization(branch, f"一键交付分支 {branch}")

    with _raises(pr_delivery.PRDeliveryError, "交付授权不匹配"):
        pr_delivery.validate_authorization(branch, "继续")


def test_delivery_rejects_master_and_invalid_topic_branches():
    for branch in ("master", "feature/test", "codex/../master", "codex/"):
        with _raises(pr_delivery.PRDeliveryError, "分支"):
            pr_delivery.validate_branch_name(branch)


def test_check_rollup_distinguishes_pending_success_and_failure():
    assert pr_delivery._check_rollup_state([])[0] == "pending"
    assert pr_delivery._check_rollup_state([{
        "__typename": "CheckRun",
        "name": "PR Checks",
        "status": "IN_PROGRESS",
        "conclusion": "",
    }]) == ("pending", "PR Checks")
    assert pr_delivery._check_rollup_state([{
        "__typename": "CheckRun",
        "name": "PR Checks",
        "status": "COMPLETED",
        "conclusion": "SUCCESS",
    }]) == ("success", "全部检查通过")
    assert pr_delivery._check_rollup_state([{
        "__typename": "CheckRun",
        "name": "PR Checks",
        "status": "COMPLETED",
        "conclusion": "FAILURE",
    }]) == ("failed", "PR Checks")


def test_execute_rejects_wrong_authorization_before_reading_or_mutating_repo():
    with patch.object(pr_delivery, "_git_text") as git_text:
        with _raises(pr_delivery.PRDeliveryError, "交付授权不匹配"):
            pr_delivery.deliver(
                "codex/test",
                execute=True,
                authorization="一键交付分支 codex/other",
            )
    git_text.assert_not_called()


def test_preflight_stops_on_divergence_instead_of_rebasing():
    def fake_git_text(*args, **_kwargs):
        if args == ("rev-parse", "HEAD"):
            return "b" * 40
        if args == ("rev-parse", "origin/master"):
            return "a" * 40
        raise AssertionError(args)

    with (
        patch.object(pr_delivery, "_current_branch", return_value="codex/test"),
        patch.object(pr_delivery, "_assert_clean_worktree"),
        patch.object(pr_delivery, "_assert_master_worktree_safe"),
        patch.object(pr_delivery, "_run", return_value=_completed()),
        patch.object(pr_delivery, "_git_text", side_effect=fake_git_text),
        patch.object(pr_delivery, "_is_ancestor", return_value=False),
    ):
        with _raises(pr_delivery.PRDeliveryError, "不会 rebase"):
            pr_delivery.preflight("codex/test", run_tests=False)


def test_wait_for_pr_checks_waits_then_accepts_clean_success():
    pending = {
        "number": 8,
        "state": "OPEN",
        "isDraft": False,
        "mergeable": "UNKNOWN",
        "mergeStateStatus": "UNSTABLE",
        "statusCheckRollup": [],
    }
    success = {
        "number": 8,
        "state": "OPEN",
        "isDraft": False,
        "mergeable": "MERGEABLE",
        "mergeStateStatus": "CLEAN",
        "statusCheckRollup": [{
            "__typename": "CheckRun",
            "name": "PR Checks",
            "status": "COMPLETED",
            "conclusion": "SUCCESS",
        }],
    }
    with (
        patch.object(pr_delivery, "_pr_view", side_effect=[pending, success]),
        patch.object(pr_delivery.time, "monotonic", return_value=0),
        patch.object(pr_delivery.time, "sleep") as sleep,
    ):
        result = pr_delivery.wait_for_pr_checks(8, timeout=10, poll_interval=1)

    assert result == success
    sleep.assert_called_once_with(1)


def test_wait_for_pr_checks_stops_before_merge_on_failure():
    failed = {
        "number": 8,
        "state": "OPEN",
        "isDraft": False,
        "mergeable": "MERGEABLE",
        "mergeStateStatus": "UNSTABLE",
        "statusCheckRollup": [{
            "__typename": "CheckRun",
            "name": "Stable regression",
            "status": "COMPLETED",
            "conclusion": "FAILURE",
        }],
    }
    with patch.object(pr_delivery, "_pr_view", return_value=failed):
        with _raises(pr_delivery.PRDeliveryError, "检查失败"):
            pr_delivery.wait_for_pr_checks(8)


def test_finalize_preserves_branches_when_gitee_is_not_synchronized():
    merge_sha = "a" * 40
    with (
        patch.object(pr_delivery, "_run", return_value=_completed()) as run,
        patch.object(
            pr_delivery,
            "_remote_ref",
            side_effect=[merge_sha, "b" * 40],
        ),
        patch.object(pr_delivery, "_update_local_master") as update_master,
        patch.object(pr_delivery, "_remote_branch_exists") as remote_exists,
        patch.object(pr_delivery, "_local_branch_exists") as local_exists,
    ):
        with _raises(pr_delivery.PRDeliveryError, "拒绝清理分支"):
            pr_delivery.finalize_delivery("codex/test", merge_sha)

    assert run.call_args_list == [
        call(["git", "fetch", "origin"]),
        call(["git", "push", "gitee", "origin/master:master"]),
    ]
    update_master.assert_not_called()
    remote_exists.assert_not_called()
    local_exists.assert_not_called()


def test_finalize_cleans_branch_only_after_both_masters_match():
    merge_sha = "a" * 40
    branch = "codex/test"
    with (
        patch.object(pr_delivery, "_run", return_value=_completed()) as run,
        patch.object(pr_delivery, "_remote_ref", side_effect=[merge_sha, merge_sha]),
        patch.object(pr_delivery, "_update_local_master") as update_master,
        patch.object(pr_delivery, "_remote_branch_exists", side_effect=[True, False]),
        patch.object(pr_delivery, "_current_branch", return_value=branch),
        patch.object(pr_delivery, "_local_branch_exists", side_effect=[True, False]),
    ):
        result = pr_delivery.finalize_delivery(branch, merge_sha)

    assert result["origin_master"] == merge_sha
    assert result["gitee_master"] == merge_sha
    update_master.assert_called_once_with(merge_sha)
    assert call(["git", "push", "origin", "--delete", branch]) in run.call_args_list
    assert call(["git", "switch", "--detach", "origin/master"]) in run.call_args_list
    assert call(["git", "branch", "-D", branch]) in run.call_args_list


def test_preview_runs_gate_without_external_delivery_mutations():
    gate = {"head_sha": "a" * 40, "commit_count": "2"}
    with (
        patch.object(pr_delivery, "_git_text", return_value="a" * 40),
        patch.object(pr_delivery, "_find_delivery_pr", return_value=None),
        patch.object(pr_delivery, "preflight", return_value=gate) as preflight,
        patch.object(pr_delivery, "_push_and_create_pr") as push,
        patch.object(pr_delivery, "_merge_pr") as merge,
        patch.object(pr_delivery, "finalize_delivery") as finalize,
    ):
        result = pr_delivery.deliver("codex/test")

    assert result == {"mode": "preview", "gate": gate}
    preflight.assert_called_once_with("codex/test")
    push.assert_not_called()
    merge.assert_not_called()
    finalize.assert_not_called()


def test_execute_runs_push_checks_merge_and_finalize_in_order():
    branch = "codex/test"
    head_sha = "a" * 40
    merge_sha = "b" * 40
    gate = {"head_sha": head_sha, "commit_count": "1"}
    pr = {"number": 8, "state": "OPEN", "headRefOid": head_sha}
    checked_pr = {
        "number": 8,
        "state": "OPEN",
        "mergeable": "MERGEABLE",
        "mergeStateStatus": "CLEAN",
    }
    merged_pr = {
        "number": 8,
        "state": "MERGED",
        "mergeCommit": {"oid": merge_sha},
    }
    events = []
    with (
        patch.object(pr_delivery, "_git_text", return_value=head_sha),
        patch.object(pr_delivery, "_find_delivery_pr", return_value=None),
        patch.object(
            pr_delivery,
            "preflight",
            side_effect=lambda *_: events.append("gate") or gate,
        ),
        patch.object(
            pr_delivery,
            "_push_and_create_pr",
            side_effect=lambda *_args, **_kwargs: events.append("push_pr") or pr,
        ),
        patch.object(
            pr_delivery,
            "wait_for_pr_checks",
            side_effect=lambda *_args, **_kwargs: events.append("checks") or checked_pr,
        ),
        patch.object(
            pr_delivery,
            "_merge_pr",
            side_effect=lambda *_: events.append("merge") or merged_pr,
        ),
        patch.object(
            pr_delivery,
            "finalize_delivery",
            side_effect=lambda *_: events.append("finalize") or {"merge_sha": merge_sha},
        ),
    ):
        result = pr_delivery.deliver(
            branch,
            execute=True,
            authorization=f"一键交付分支 {branch}",
        )

    assert events == ["gate", "push_pr", "checks", "merge", "finalize"]
    assert result["merge_sha"] == merge_sha


def test_execute_resumes_merged_pr_at_sync_and_cleanup():
    branch = "codex/test"
    head_sha = "a" * 40
    merge_sha = "b" * 40
    merged_pr = {
        "number": 8,
        "state": "MERGED",
        "headRefOid": head_sha,
        "mergeCommit": {"oid": merge_sha},
    }
    with (
        patch.object(pr_delivery, "_git_text", return_value=head_sha),
        patch.object(pr_delivery, "_find_delivery_pr", return_value=merged_pr),
        patch.object(pr_delivery, "preflight") as preflight,
        patch.object(
            pr_delivery,
            "finalize_delivery",
            return_value={"merge_sha": merge_sha},
        ) as finalize,
    ):
        result = pr_delivery.deliver(
            branch,
            execute=True,
            authorization=f"一键交付分支 {branch}",
        )

    preflight.assert_not_called()
    finalize.assert_called_once_with(branch, merge_sha)
    assert result["merge_sha"] == merge_sha
