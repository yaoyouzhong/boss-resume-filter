import importlib.util
import subprocess
from pathlib import Path
from unittest.mock import Mock, call, patch


BASE_DIR = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "boss_resume_filter_release_retry",
    BASE_DIR / "scripts" / "release_retry.py",
)
assert SPEC and SPEC.loader
release_retry = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(release_retry)


def _completed(returncode: int, stdout: str = "", stderr: str = ""):
    return subprocess.CompletedProcess([], returncode, stdout, stderr)


def test_cli_retry_uses_one_initial_attempt_plus_three_retries():
    run = Mock(side_effect=[
        _completed(1, stderr="temporary 1"),
        _completed(1, stderr="temporary 2"),
        _completed(1, stderr="temporary 3"),
        _completed(0),
    ])
    with patch.object(release_retry.time, "sleep") as sleep:
        result = release_retry.run_cli_with_retries(run, ["gh"], "查询")

    assert result.returncode == 0
    assert run.call_count == 4
    assert sleep.call_args_list == [call(2), call(4), call(6)]


def test_cli_retry_stops_immediately_on_deterministic_failure():
    run = Mock(return_value=_completed(1, stderr="HTTP 403 permission denied"))
    with patch.object(release_retry.time, "sleep") as sleep:
        result = release_retry.run_cli_with_retries(run, ["gh"], "查询")

    assert result.returncode == 1
    run.assert_called_once()
    sleep.assert_not_called()


def test_cli_retry_does_not_repeat_validation_failure():
    run = Mock(return_value=_completed(1, stderr="HTTP 422 validation failed"))
    with patch.object(release_retry.time, "sleep") as sleep:
        result = release_retry.run_cli_with_retries(run, ["gh"], "创建")

    assert result.returncode == 1
    run.assert_called_once()
    sleep.assert_not_called()


def test_cli_retry_accepts_successful_postcondition_after_lost_response():
    run = Mock(return_value=_completed(1, stderr="connection reset"))
    postcondition = Mock(return_value=True)
    with patch.object(release_retry.time, "sleep") as sleep:
        result = release_retry.run_cli_with_retries(
            run,
            ["git", "push"],
            "推送",
            postcondition=postcondition,
        )

    assert result.returncode == 0
    run.assert_called_once()
    postcondition.assert_called_once()
    sleep.assert_not_called()


def test_json_query_retries_malformed_output():
    run = Mock(side_effect=[
        _completed(0, stdout="{"),
        _completed(0, stdout='{"state":"OPEN"}'),
    ])
    with patch.object(release_retry.time, "sleep") as sleep:
        result = release_retry.run_json_query_with_retries(
            run, ["gh", "pr", "view"], "读取 PR"
        )

    assert result == {"state": "OPEN"}
    sleep.assert_called_once_with(2)
