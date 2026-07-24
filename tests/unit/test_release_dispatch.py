import importlib.util
from argparse import Namespace
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch


BASE_DIR = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "boss_resume_filter_release_dispatch",
    BASE_DIR / "scripts" / "release_dispatch.py",
)
assert SPEC and SPEC.loader
release_dispatch = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(release_dispatch)


@contextmanager
def _raises(error_type, message: str):
    try:
        yield
    except error_type as exc:
        assert message in str(exc)
    else:
        raise AssertionError(f"expected {error_type.__name__}: {message}")


def _plan(*, published: bool = False, staged: bool | None = None, runs=None):
    staged = published if staged is None else staged
    return {
        "version": "2.22",
        "release_sha": "a" * 40,
        "tag": "v2.22",
        "resume": "true" if staged else "false",
        "needs_windows": "false" if staged else "true",
        "needs_macos": "false" if staged else "true",
        "release_title": "v2.22 — Test",
        "release_body": "### 新增功能\n\n- **Test**：Description",
        "content_sha": "c" * 64,
        "staged": staged,
        "published": published,
        "verification": (
            {"manifest_sha": "a" * 40, "verified_at": "test"}
            if published else {}
        ),
        "runs": runs or [],
    }


def _run(run_id: int = 100, *, status: str = "queued", conclusion: str = ""):
    return {
        "databaseId": run_id,
        "displayTitle": "Release v2.22",
        "headSha": "a" * 40,
        "status": status,
        "conclusion": conclusion,
        "url": f"https://github.example/actions/runs/{run_id}",
        "jobs": [],
    }


def test_formal_release_authorization_must_match_before_preflight():
    with patch.object(release_dispatch, "preflight") as preflight:
        with _raises(release_dispatch.ReleaseDispatchError, "授权不匹配"):
            release_dispatch.dispatch_release(
                "2.22",
                execute=True,
                authorization="继续",
            )
    preflight.assert_not_called()


def test_main_switches_to_pack_venv_before_formal_release():
    events = []
    args = Namespace(
        version="2.22",
        execute=False,
        authorization="",
        approved_content_sha="",
        timeout=30,
        poll_interval=2,
        stall_timeout=10,
    )
    with (
        patch.object(
            release_dispatch.release_prepare.build,
            "run_in_venv",
            side_effect=lambda *_: events.append("venv"),
        ) as run_in_venv,
        patch.object(release_dispatch, "_build_parser") as build_parser,
        patch.object(
            release_dispatch,
            "dispatch_release",
            side_effect=lambda *_args, **_kwargs: events.append("dispatch"),
        ),
    ):
        build_parser.return_value.parse_args.return_value = args
        assert release_dispatch.main() == 0

    run_in_venv.assert_called_once_with(release_dispatch.__file__)
    assert events == ["venv", "dispatch"]


def test_matching_runs_requires_both_version_and_immutable_sha():
    runs = [
        _run(1),
        {**_run(2), "displayTitle": "Release v2.21"},
        {**_run(3), "headSha": "b" * 40},
        {**_run(4), "displayTitle": "Release v2.22 (Dry Run)"},
    ]
    assert release_dispatch._matching_runs(runs, "2.22", "a" * 40) == [runs[0]]


def test_preflight_reuses_hosted_prepare_contract_after_local_remote_checks():
    def fake_git_text(*args):
        if args == ("branch", "--show-current"):
            return "master"
        if args == ("status", "--porcelain"):
            return ""
        if args in {
            ("rev-parse", "HEAD"),
            ("rev-parse", "origin/master"),
            ("rev-parse", "gitee/master"),
        }:
            return "a" * 40
        raise AssertionError(args)

    gate = {
        "release_sha": "a" * 40,
        "tag": "v2.22",
        "resume": "false",
        "needs_windows": "true",
        "needs_macos": "true",
        "release_title": "v2.22 — Test",
        "release_body": "### 新增功能\n\n- **Test**：Description",
        "content_sha": "c" * 64,
    }
    with (
        patch.object(release_dispatch, "_git_text", side_effect=fake_git_text),
        patch.object(
            release_dispatch,
            "_run",
            return_value=release_dispatch.subprocess.CompletedProcess([], 0, "", ""),
        ) as run,
        patch.object(
            release_dispatch.release_ci,
            "prepare_release",
            return_value=gate,
        ) as prepare,
        patch.object(release_dispatch, "_list_release_runs", return_value=[]),
    ):
        result = release_dispatch.preflight("2.22")

    assert result["release_sha"] == "a" * 40
    prepare.assert_called_once_with(
        "2.22",
        "确认正式发布 v2.22",
        approved_content_sha="",
        dry_run=True,
        reuse_reviewed_gate=False,
    )
    assert run.call_count == 3


def test_preflight_public_resume_checks_all_downloads_before_issuing_receipt():
    gate = {
        "release_sha": "a" * 40,
        "tag": "v2.22",
        "resume": "true",
        "needs_windows": "false",
        "needs_macos": "false",
        "release_title": "v2.22 — Test",
        "release_body": "### 新增功能\n\n- **Test**：Description",
        "content_sha": "c" * 64,
    }

    def fake_git_text(*args):
        if args == ("branch", "--show-current"):
            return "master"
        if args == ("status", "--porcelain"):
            return ""
        if args in {
            ("rev-parse", "HEAD"),
            ("rev-parse", "origin/master"),
            ("rev-parse", "gitee/master"),
        }:
            return "a" * 40
        raise AssertionError(args)

    with (
        patch.object(release_dispatch, "_git_text", side_effect=fake_git_text),
        patch.object(
            release_dispatch,
            "_run",
            return_value=release_dispatch.subprocess.CompletedProcess([], 0, "", ""),
        ),
        patch.object(release_dispatch.release_ci, "prepare_release", return_value=gate),
        patch.object(release_dispatch, "_list_release_runs", return_value=[]),
        patch.object(
            release_dispatch.build,
            "_verify_release_remote_state",
            return_value=True,
        ),
        patch.object(
            release_dispatch.release_ci,
            "verify_public_endpoints",
            return_value={"downloads_checked": 6, "manifests_checked": 2},
        ) as verify_public,
    ):
        result = release_dispatch.preflight("2.22")

    assert result["published"] is True
    assert result["verification"]["manifest_sha"] == "a" * 40
    assert result["verification"]["downloads_checked"] == 6
    assert result["verification"]["manifests_checked"] == 2
    verify_public.assert_called_once_with("2.22")


def test_preview_never_dispatches_or_waits():
    plan = _plan()
    with (
        patch.object(release_dispatch, "preflight", return_value=plan),
        patch.object(release_dispatch, "_dispatch_workflow") as dispatch,
        patch.object(release_dispatch, "wait_for_run") as wait,
    ):
        result = release_dispatch.dispatch_release("2.22")

    assert result == {"mode": "preview", "plan": plan}
    dispatch.assert_not_called()
    wait.assert_not_called()


def test_execute_requires_the_exact_content_review_evidence():
    plan = _plan()
    with (
        patch.object(release_dispatch, "preflight", return_value=plan),
        patch.object(release_dispatch, "_dispatch_workflow") as dispatch,
    ):
        with _raises(release_dispatch.ReleaseDispatchError, "必须重新展示并确认"):
            release_dispatch.dispatch_release(
                "2.22",
                execute=True,
                authorization="确认正式发布 v2.22",
                approved_content_sha="d" * 64,
            )
    dispatch.assert_not_called()


def test_completed_public_release_is_verified_without_dispatching_again():
    plan = _plan(published=True)
    finished = {"mode": "already_published"}
    with (
        patch.object(release_dispatch, "preflight", return_value=plan),
        patch.object(release_dispatch, "_finish_success", return_value=finished) as finish,
        patch.object(release_dispatch, "_dispatch_workflow") as dispatch,
    ):
        result = release_dispatch.dispatch_release(
            "2.22",
            execute=True,
            authorization="确认正式发布 v2.22",
            approved_content_sha="c" * 64,
        )

    assert result == finished
    finish.assert_called_once_with(
        "2.22",
        None,
        already_published=True,
        verification=plan["verification"],
    )
    dispatch.assert_not_called()


def test_execute_reuses_active_matching_run_instead_of_dispatching_duplicate():
    active = _run(101, status="in_progress")
    plan = _plan(runs=[active])
    completed = _run(101, status="completed", conclusion="success")
    with (
        patch.object(release_dispatch, "preflight", return_value=plan),
        patch.object(release_dispatch.release_ci, "require_local_gitee_access"),
        patch.object(release_dispatch, "_dispatch_workflow") as dispatch,
        patch.object(release_dispatch.release_ci, "record_actions_run"),
        patch.object(release_dispatch, "wait_for_run", return_value=completed) as wait,
        patch.object(
            release_dispatch,
            "_finish_success",
            return_value={"mode": "published"},
        ) as finish,
        patch.object(
            release_dispatch.release_ci,
            "finalize_release_local",
            return_value={},
        ) as finalize,
    ):
        result = release_dispatch.dispatch_release(
            "2.22",
            execute=True,
            authorization="确认正式发布 v2.22",
            approved_content_sha="c" * 64,
        )

    assert result["mode"] == "published"
    dispatch.assert_not_called()
    wait.assert_called_once_with(
        101,
        timeout=release_dispatch.DEFAULT_RELEASE_TIMEOUT,
        poll_interval=release_dispatch.DEFAULT_POLL_INTERVAL,
        stall_timeout=release_dispatch.DEFAULT_ACTION_STALL_TIMEOUT,
    )
    finalize.assert_called_once_with(
        "2.22", "确认正式发布 v2.22", "a" * 40, "c" * 64,
    )
    finish.assert_called_once_with("2.22", completed, verification={})


def test_execute_reruns_only_failed_jobs_for_matching_release():
    failed = _run(101, status="completed", conclusion="failure")
    restarted = _run(101, status="in_progress")
    completed = _run(101, status="completed", conclusion="success")
    plan = _plan(runs=[failed])
    with (
        patch.object(release_dispatch, "preflight", return_value=plan),
        patch.object(release_dispatch.release_ci, "require_local_gitee_access"),
        patch.object(
            release_dispatch,
            "_rerun_failed_jobs",
            return_value=restarted,
        ) as rerun,
        patch.object(release_dispatch, "_dispatch_workflow") as dispatch,
        patch.object(
            release_dispatch,
            "wait_for_run",
            return_value=completed,
        ),
        patch.object(release_dispatch.release_ci, "record_actions_run"),
        patch.object(
            release_dispatch.release_ci,
            "finalize_release_local",
            return_value={},
        ),
        patch.object(
            release_dispatch,
            "_finish_success",
            return_value={"mode": "published"},
        ),
    ):
        result = release_dispatch.dispatch_release(
            "2.22",
            execute=True,
            authorization="确认正式发布 v2.22",
            approved_content_sha="c" * 64,
        )

    assert result["mode"] == "published"
    rerun.assert_called_once_with(failed)
    dispatch.assert_not_called()


def test_execute_snapshots_runs_dispatches_discovers_waits_and_finishes():
    plan = _plan()
    old = _run(99, status="completed", conclusion="failure")
    created = _run(102, status="queued")
    completed = _run(102, status="completed", conclusion="success")
    events = []
    with (
        patch.object(release_dispatch, "preflight", return_value=plan),
        patch.object(
            release_dispatch.release_ci,
            "require_local_gitee_access",
            side_effect=lambda: events.append("local_access") or "token",
        ),
        patch.object(release_dispatch, "_list_release_runs", return_value=[old]),
        patch.object(
            release_dispatch,
            "_dispatch_workflow",
            side_effect=lambda *_: events.append("dispatch"),
        ) as dispatch,
        patch.object(
            release_dispatch,
            "_discover_new_run",
            side_effect=lambda *_args, **_kwargs: events.append("discover") or created,
        ) as discover,
        patch.object(
            release_dispatch,
            "wait_for_run",
            side_effect=lambda *_args, **_kwargs: events.append("wait") or completed,
        ),
        patch.object(
            release_dispatch,
            "_finish_success",
            side_effect=lambda *_args, **_kwargs: events.append("finish")
            or {"mode": "published"},
        ),
        patch.object(release_dispatch.release_ci, "record_actions_run"),
        patch.object(
            release_dispatch.release_ci,
            "finalize_release_local",
            side_effect=lambda *_args: events.append("finalize"),
        ),
    ):
        result = release_dispatch.dispatch_release(
            "2.22",
            execute=True,
            authorization="确认正式发布 v2.22",
            approved_content_sha="c" * 64,
            timeout=100,
            poll_interval=2,
        )

    assert result["mode"] == "published"
    assert events == ["local_access", "dispatch", "discover", "wait", "finalize", "finish"]
    dispatch.assert_called_once_with(
        "2.22", "确认正式发布 v2.22", "c" * 64,
    )
    discover.assert_called_once_with("2.22", "a" * 40, {99})


def test_staged_github_release_skips_actions_and_finalizes_locally():
    plan = _plan(staged=True)
    events = []
    with (
        patch.object(release_dispatch, "preflight", return_value=plan),
        patch.object(release_dispatch.release_ci, "require_local_gitee_access"),
        patch.object(release_dispatch, "_dispatch_workflow") as dispatch,
        patch.object(release_dispatch, "wait_for_run") as wait,
        patch.object(
            release_dispatch.release_ci,
            "finalize_release_local",
            side_effect=lambda *_: events.append("finalize"),
        ) as finalize,
        patch.object(
            release_dispatch,
            "_finish_success",
            side_effect=lambda *_args, **_kwargs: events.append("finish")
            or {"mode": "published"},
        ),
    ):
        result = release_dispatch.dispatch_release(
            "2.22",
            execute=True,
            authorization="确认正式发布 v2.22",
            approved_content_sha="c" * 64,
        )

    assert result["mode"] == "published"
    assert events == ["finalize", "finish"]
    dispatch.assert_not_called()
    wait.assert_not_called()
    finalize.assert_called_once_with(
        "2.22", "确认正式发布 v2.22", "a" * 40, "c" * 64,
    )


def test_wait_for_run_reports_progress_then_accepts_only_success():
    queued = _run(100, status="queued")
    success = _run(100, status="completed", conclusion="success")
    with (
        patch.object(release_dispatch, "_run_view", side_effect=[queued, success]),
        patch.object(release_dispatch.time, "monotonic", return_value=0),
        patch.object(release_dispatch.time, "sleep") as sleep,
        patch.object(release_dispatch, "_print_progress"),
    ):
        result = release_dispatch.wait_for_run(100, timeout=10, poll_interval=1)
    assert result == success
    sleep.assert_called_once_with(1)

    failed = _run(100, status="completed", conclusion="failure")
    with patch.object(release_dispatch, "_run_view", return_value=failed):
        with _raises(release_dispatch.ReleaseDispatchError, "同一版本安全续跑"):
            release_dispatch.wait_for_run(100)


def test_github_run_query_retries_transient_failure():
    failed = release_dispatch.subprocess.CompletedProcess(
        ["gh"], 1, stdout="", stderr="temporary network failure",
    )
    succeeded = release_dispatch.subprocess.CompletedProcess(
        ["gh"], 0, stdout="[]", stderr="",
    )
    with (
        patch.object(release_dispatch, "_run", side_effect=[failed, succeeded]) as run,
        patch.object(release_dispatch.time, "sleep") as sleep,
    ):
        assert release_dispatch._list_release_runs() == []

    assert run.call_count == 2
    sleep.assert_called_once_with(2)


def test_workflow_dispatch_accepts_lost_response_when_run_was_created():
    created = _run(123)
    failed = release_dispatch.subprocess.CompletedProcess(
        ["gh"], 1, stdout="", stderr="connection reset"
    )
    with (
        patch.object(
            release_dispatch,
            "_git_text",
            return_value="a" * 40,
        ),
        patch.object(
            release_dispatch,
            "_list_release_runs",
            side_effect=[[], [created]],
        ),
        patch.object(release_dispatch, "_run", return_value=failed) as run,
        patch.object(release_dispatch.time, "sleep") as sleep,
    ):
        release_dispatch._dispatch_workflow(
            "2.22", "确认正式发布 v2.22", "c" * 64
        )

    run.assert_called_once()
    sleep.assert_not_called()


def test_wait_for_run_stops_after_no_stage_progress():
    queued = _run(100, status="in_progress")
    with (
        patch.object(release_dispatch, "_run_view", return_value=queued),
        patch.object(release_dispatch.time, "monotonic", side_effect=[0, 0, 6]),
        patch.object(release_dispatch.time, "sleep"),
        patch.object(release_dispatch, "_print_progress"),
        _raises(release_dispatch.ReleaseDispatchError, "无阶段变化"),
    ):
        release_dispatch.wait_for_run(
            100,
            timeout=100,
            poll_interval=1,
            stall_timeout=5,
        )
