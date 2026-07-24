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


def test_gitee_master_push_skips_when_remote_is_already_at_target():
    merge_sha = "a" * 40
    with (
        patch.object(pr_delivery, "_remote_ref", return_value=merge_sha),
        patch.object(pr_delivery, "_run") as run,
    ):
        assert pr_delivery._push_gitee_master(merge_sha) == merge_sha

    run.assert_not_called()


def test_gitee_master_same_value_push_race_is_idempotent_success():
    merge_sha = "a" * 40
    failed = subprocess.CompletedProcess(
        ["git", "push"], 1, stdout="", stderr="incorrect old value provided",
    )
    with (
        patch.object(
            pr_delivery,
            "_remote_ref",
            side_effect=["b" * 40, merge_sha, merge_sha],
        ),
        patch.object(pr_delivery, "_run", return_value=failed),
    ):
        assert pr_delivery._push_gitee_master(merge_sha) == merge_sha


def test_gitee_master_push_failure_still_blocks_when_remote_differs():
    merge_sha = "a" * 40
    failed = subprocess.CompletedProcess(
        ["git", "push"], 1, stdout="", stderr="incorrect old value provided",
    )
    with (
        patch.object(pr_delivery, "_remote_ref", return_value="b" * 40),
        patch.object(pr_delivery, "_run", return_value=failed),
        patch.object(pr_delivery.release_retry.time, "sleep"),
    ):
        with _raises(pr_delivery.PRDeliveryError, "incorrect old value provided"):
            pr_delivery._push_gitee_master(merge_sha)


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


def test_preflight_rechecks_worktrees_after_local_gate():
    def fake_git_text(*args, **_kwargs):
        if args == ("rev-parse", "HEAD"):
            return "b" * 40
        if args == ("rev-parse", "origin/master"):
            return "a" * 40
        raise AssertionError(args)

    dirty_error = pr_delivery.PRDeliveryError("本地门禁后当前工作区存在未提交修改")
    with (
        patch.object(pr_delivery, "_current_branch", return_value="codex/test"),
        patch.object(
            pr_delivery,
            "_assert_delivery_worktrees_clean",
            side_effect=[None, dirty_error],
        ) as assert_clean,
        patch.object(pr_delivery, "_run", return_value=_completed()),
        patch.object(pr_delivery, "_git_text", side_effect=fake_git_text),
        patch.object(pr_delivery, "_is_ancestor", return_value=True),
        patch.object(pr_delivery, "_run_local_gate") as local_gate,
    ):
        with _raises(pr_delivery.PRDeliveryError, "本地门禁后当前工作区"):
            pr_delivery.preflight("codex/test")

    local_gate.assert_called_once_with()
    assert_clean.assert_has_calls([
        call("当前工作区"),
        call("本地门禁后当前工作区"),
    ])


def test_execute_stops_before_push_when_local_gate_dirties_worktree():
    branch = "codex/test"
    with (
        patch.object(pr_delivery, "_git_text", return_value="a" * 40),
        patch.object(pr_delivery, "_find_delivery_pr", return_value=None),
        patch.object(
            pr_delivery,
            "preflight",
            side_effect=pr_delivery.PRDeliveryError("本地门禁后当前工作区存在未提交修改"),
        ),
        patch.object(pr_delivery, "_push_and_create_pr") as push,
    ):
        with _raises(pr_delivery.PRDeliveryError, "本地门禁后当前工作区"):
            pr_delivery.deliver(
                branch,
                execute=True,
                authorization=f"一键交付分支 {branch}",
            )

    push.assert_not_called()


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


def test_wait_for_pr_checks_fails_fast_when_no_checks_are_created():
    clean_without_checks = {
        "number": 8,
        "state": "OPEN",
        "isDraft": False,
        "mergeable": "MERGEABLE",
        "mergeStateStatus": "CLEAN",
        "statusCheckRollup": [],
    }
    with (
        patch.object(pr_delivery, "_pr_view", return_value=clean_without_checks),
        patch.object(pr_delivery.time, "monotonic", side_effect=[0, 0, 2]),
        patch.object(pr_delivery.time, "sleep") as sleep,
    ):
        with _raises(pr_delivery.PRDeliveryError, "未发现任何 PR Checks"):
            pr_delivery.wait_for_pr_checks(
                8,
                timeout=10,
                poll_interval=1,
                check_startup_timeout=1,
            )

    sleep.assert_called_once_with(1)


def test_wait_for_pr_checks_waits_for_expected_head_sha_before_checks():
    stale = {
        "number": 8,
        "state": "OPEN",
        "isDraft": False,
        "headRefOid": "a" * 40,
        "mergeable": "MERGEABLE",
        "mergeStateStatus": "CLEAN",
        "statusCheckRollup": [],
    }
    with (
        patch.object(pr_delivery, "_pr_view", return_value=stale),
        patch.object(pr_delivery.time, "monotonic", side_effect=[0, 0, 2]),
        patch.object(pr_delivery.time, "sleep") as sleep,
    ):
        with _raises(pr_delivery.PRDeliveryError, "head 未同步"):
            pr_delivery.wait_for_pr_checks(
                8,
                timeout=10,
                poll_interval=1,
                check_startup_timeout=1,
                expected_head_sha="b" * 40,
            )

    sleep.assert_called_once_with(1)


def test_wait_for_pr_checks_uses_actions_run_when_rollup_is_stale():
    stale_rollup = {
        "number": 8,
        "state": "OPEN",
        "isDraft": False,
        "headRefName": "codex/test",
        "headRefOid": "a" * 40,
        "mergeable": "MERGEABLE",
        "mergeStateStatus": "UNSTABLE",
        "statusCheckRollup": [{
            "__typename": "CheckRun",
            "name": "Stable regression",
            "status": "IN_PROGRESS",
            "conclusion": "",
        }],
    }
    with (
        patch.object(pr_delivery, "_pr_view", return_value=stale_rollup),
        patch.object(
            pr_delivery,
            "_pull_request_run_state_for_head",
            return_value=("success", "GitHub Actions run 已成功"),
        ) as run_state,
        patch.object(pr_delivery.time, "monotonic", return_value=0),
        patch.object(pr_delivery.time, "sleep") as sleep,
    ):
        result = pr_delivery.wait_for_pr_checks(
            8,
            timeout=10,
            poll_interval=1,
            expected_head_sha="a" * 40,
        )

    assert result == stale_rollup
    run_state.assert_called_once_with("codex/test", "a" * 40)
    sleep.assert_not_called()


def test_finalize_preserves_branches_when_gitee_is_not_synchronized():
    merge_sha = "a" * 40
    with (
        patch.object(pr_delivery, "_assert_delivery_worktrees_clean"),
        patch.object(pr_delivery, "_run", return_value=_completed()) as run,
        patch.object(pr_delivery, "_remote_ref", return_value=merge_sha),
        patch.object(
            pr_delivery,
            "_push_gitee_master",
            side_effect=pr_delivery.PRDeliveryError("Gitee master 同步失败"),
        ),
        patch.object(
            pr_delivery, "_update_local_master", return_value=True
        ) as update_master,
        patch.object(pr_delivery, "_remote_branch_exists") as remote_exists,
        patch.object(pr_delivery, "_local_branch_exists") as local_exists,
    ):
        with _raises(pr_delivery.PRDeliveryError, "同步失败"):
            pr_delivery.finalize_delivery("codex/test", merge_sha)

    assert [item.args[0] for item in run.call_args_list] == [
        ["git", "fetch", "origin"]
    ]
    update_master.assert_not_called()
    remote_exists.assert_not_called()
    local_exists.assert_not_called()


def test_finalize_stops_before_sync_when_worktree_is_already_dirty():
    with (
        patch.object(
            pr_delivery,
            "_assert_delivery_worktrees_clean",
            side_effect=pr_delivery.PRDeliveryError("PR 合并后当前工作区存在未提交修改"),
        ),
        patch.object(pr_delivery, "_run") as run,
    ):
        with _raises(pr_delivery.PRDeliveryError, "PR 合并后当前工作区"):
            pr_delivery.finalize_delivery("codex/test", "a" * 40)

    run.assert_not_called()


def test_finalize_cleans_branch_only_after_both_masters_match():
    merge_sha = "a" * 40
    branch = "codex/test"
    with (
        patch.object(pr_delivery, "_assert_delivery_worktrees_clean"),
        patch.object(pr_delivery, "_run", return_value=_completed()) as run,
        patch.object(pr_delivery, "_remote_ref", return_value=merge_sha),
        patch.object(pr_delivery, "_push_gitee_master", return_value=merge_sha),
        patch.object(pr_delivery, "_update_local_master", return_value=True) as update_master,
        patch.object(pr_delivery, "_worktree_for_branch", return_value=Path("D:/master")),
        patch.object(pr_delivery, "_remote_branch_exists", side_effect=[True, False]),
        patch.object(pr_delivery, "_current_branch", return_value=branch),
        patch.object(pr_delivery, "_local_branch_exists", side_effect=[True, False]),
    ):
        result = pr_delivery.finalize_delivery(branch, merge_sha)

    assert result["origin_master"] == merge_sha
    assert result["gitee_master"] == merge_sha
    update_master.assert_called_once_with(merge_sha)
    assert ["git", "push", "origin", "--delete", branch] in [
        item.args[0] for item in run.call_args_list
    ]
    assert call(["git", "switch", "--detach", "origin/master"]) in run.call_args_list
    assert call(["git", "branch", "-D", branch]) in run.call_args_list


def test_finalize_preserves_branch_when_worktree_gets_dirty_before_cleanup():
    merge_sha = "a" * 40
    dirty_error = pr_delivery.PRDeliveryError("分支清理前当前工作区存在未提交修改")
    with (
        patch.object(
            pr_delivery,
            "_assert_delivery_worktrees_clean",
            side_effect=[None, dirty_error],
        ),
        patch.object(pr_delivery, "_run", return_value=_completed()) as run,
        patch.object(pr_delivery, "_remote_ref", return_value=merge_sha),
        patch.object(pr_delivery, "_push_gitee_master", return_value=merge_sha) as push_gitee,
        patch.object(pr_delivery, "_update_local_master", return_value=False),
        patch.object(pr_delivery, "_remote_branch_exists") as remote_exists,
        patch.object(pr_delivery, "_local_branch_exists") as local_exists,
    ):
        with _raises(pr_delivery.PRDeliveryError, "分支清理前当前工作区"):
            pr_delivery.finalize_delivery("codex/test", merge_sha)

    push_gitee.assert_called_once_with(merge_sha)
    assert not any(
        args.args[0][:3] == ["git", "push", "origin"]
        for args in run.call_args_list
    )
    assert not any(
        args.args[0][:2] == ["git", "switch"]
        for args in run.call_args_list
    )
    remote_exists.assert_not_called()
    local_exists.assert_not_called()


def test_finalize_returns_primary_worktree_to_master_before_deleting_branch():
    merge_sha = "a" * 40
    branch = "codex/test"
    with (
        patch.object(pr_delivery, "_assert_delivery_worktrees_clean"),
        patch.object(pr_delivery, "_run", return_value=_completed()) as run,
        patch.object(pr_delivery, "_remote_ref", return_value=merge_sha),
        patch.object(pr_delivery, "_push_gitee_master", return_value=merge_sha),
        patch.object(pr_delivery, "_update_local_master", return_value=False),
        patch.object(pr_delivery, "_remote_branch_exists", side_effect=[False, False]),
        patch.object(pr_delivery, "_current_branch", return_value=branch),
        patch.object(pr_delivery, "_local_branch_exists", side_effect=[True, False]),
    ):
        pr_delivery.finalize_delivery(branch, merge_sha)

    assert call(["git", "switch", "master"]) in run.call_args_list
    assert call(["git", "switch", "--detach", "origin/master"]) not in run.call_args_list
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


def test_pr_creation_retries_transient_new_branch_propagation_failure():
    branch = "codex/test"
    head_sha = "a" * 40
    pr = {"number": 10, "state": "OPEN", "headRefOid": head_sha}
    run_results = [
        _completed(),
        subprocess.CompletedProcess([], 1, "", "head sha can't be blank"),
        _completed("https://github.example/pr/10\n"),
    ]
    with (
        patch.object(pr_delivery, "_run", side_effect=run_results),
        patch.object(pr_delivery, "_find_delivery_pr", side_effect=[None, None, pr]),
        patch.object(pr_delivery, "_default_pr_title", return_value="Title"),
        patch.object(pr_delivery, "_default_pr_body", return_value="Body"),
        patch.object(pr_delivery.time, "sleep") as sleep,
    ):
        result = pr_delivery._push_and_create_pr(branch, head_sha)

    assert result == pr
    sleep.assert_called_once_with(2)


def test_existing_open_pr_is_reused_even_when_head_ref_is_stale():
    branch = "codex/test"
    target_head = "b" * 40
    existing = {
        "number": 10,
        "state": "OPEN",
        "isDraft": False,
        "url": "https://github.example/pr/10",
        "headRefOid": "a" * 40,
    }
    with patch.object(
        pr_delivery.release_retry,
        "run_json_query_with_retries",
        return_value=[existing],
    ):
        result = pr_delivery._find_delivery_pr(branch, target_head)

    assert result == existing


def test_pr_creation_reports_final_github_error_after_three_retries():
    branch = "codex/test"
    failures = [
        _completed(),
        subprocess.CompletedProcess([], 1, "", "temporary error 1"),
        subprocess.CompletedProcess([], 1, "", "temporary error 2"),
        subprocess.CompletedProcess([], 1, "", "temporary error 3"),
        subprocess.CompletedProcess([], 1, "", "final GitHub error"),
    ]
    with (
        patch.object(pr_delivery, "_run", side_effect=failures),
        patch.object(pr_delivery, "_find_delivery_pr", return_value=None),
        patch.object(pr_delivery, "_default_pr_title", return_value="Title"),
        patch.object(pr_delivery, "_default_pr_body", return_value="Body"),
        patch.object(pr_delivery.time, "sleep"),
    ):
        with _raises(pr_delivery.PRDeliveryError, "final GitHub error"):
            pr_delivery._push_and_create_pr(branch, "a" * 40)


def test_pr_view_retries_three_transient_failures_then_recovers():
    failures = [
        subprocess.CompletedProcess([], 1, "", f"temporary error {index}")
        for index in range(1, 4)
    ]
    success = _completed('{"number":28,"state":"OPEN"}')
    with (
        patch.object(pr_delivery, "_run", side_effect=[*failures, success]) as run,
        patch.object(pr_delivery.release_retry.time, "sleep") as sleep,
    ):
        result = pr_delivery._pr_view(28)

    assert result == {"number": 28, "state": "OPEN"}
    assert run.call_count == 4
    assert sleep.call_args_list == [call(2), call(4), call(6)]
