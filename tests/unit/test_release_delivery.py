import importlib.util
from argparse import Namespace
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch


BASE_DIR = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "boss_resume_filter_release_delivery",
    BASE_DIR / "scripts" / "release_delivery.py",
)
assert SPEC and SPEC.loader
release_delivery = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(release_delivery)


@contextmanager
def _raises(error_type, message: str):
    try:
        yield
    except error_type as exc:
        assert message in str(exc)
    else:
        raise AssertionError(f"expected {error_type.__name__}: {message}")


def _plan(state: str = "new"):
    return {
        "version": "2.22",
        "branch": "master",
        "release_branch": "codex/release-v2.22",
        "state": state,
        "head_sha": "a" * 40,
        "master_sha": "a" * 40,
        "base_version": "2.21",
        "working_version": "2.21",
        "last_tag": "v2.21",
        "commits": [{"sha": "b" * 40, "subject": "feat: test"}],
        "changed_files": ["gui_main.py"],
        "dirty_paths": [],
    }


def test_combined_authorization_must_match_before_repository_inspection():
    with patch.object(release_delivery.release_prepare, "inspect_repository") as inspect:
        with _raises(release_delivery.ReleaseDeliveryError, "授权不匹配"):
            release_delivery.deliver_release_preparation(
                "2.22",
                execute=True,
                authorization="一键准备版本 v2.22",
            )
    inspect.assert_not_called()


def test_main_switches_to_pack_venv_before_combined_delivery():
    events = []
    args = Namespace(
        version="2.22",
        notes_file=None,
        execute=False,
        authorization="",
        timeout=30,
        poll_interval=2,
    )
    with (
        patch.object(
            release_delivery.release_prepare.build,
            "run_in_venv",
            side_effect=lambda *_: events.append("venv"),
        ) as run_in_venv,
        patch.object(release_delivery, "_build_parser") as build_parser,
        patch.object(
            release_delivery,
            "deliver_release_preparation",
            side_effect=lambda *_args, **_kwargs: events.append("deliver"),
        ),
    ):
        build_parser.return_value.parse_args.return_value = args
        assert release_delivery.main() == 0

    run_in_venv.assert_called_once_with(release_delivery.__file__)
    assert events == ["venv", "deliver"]


def test_preview_only_inspects_and_does_not_enter_mutating_executors():
    plan = _plan()
    with (
        patch.object(
            release_delivery.release_prepare,
            "inspect_repository",
            return_value=plan,
        ) as inspect,
        patch.object(release_delivery.release_prepare, "prepare_release") as prepare,
        patch.object(release_delivery.pr_delivery, "deliver") as deliver,
    ):
        result = release_delivery.deliver_release_preparation("2.22")

    assert result == {
        "mode": "preview",
        "plan": plan,
        "branch": "codex/release-v2.22",
    }
    inspect.assert_called_once_with("2.22")
    prepare.assert_not_called()
    deliver.assert_not_called()


def test_execute_composes_existing_transactions_in_order_with_internal_authorizations():
    events = []
    prepared = {"mode": "prepared", "commit_sha": "a" * 40}
    delivered = {"mode": "execute", "merge_sha": "b" * 40}

    def fake_prepare(*args, **kwargs):
        events.append("prepare")
        assert args == ("2.22",)
        assert kwargs["authorization"] == "一键准备版本 v2.22"
        assert kwargs["show_next_step"] is False
        return prepared

    def fake_deliver(*args, **kwargs):
        events.append("deliver")
        assert args == ("codex/release-v2.22",)
        assert kwargs["authorization"] == "一键交付分支 codex/release-v2.22"
        assert kwargs["title"] == "chore: 准备 v2.22 正式发布"
        assert kwargs["run_local_tests"] is False
        return delivered

    with (
        patch.object(
            release_delivery.release_prepare,
            "prepare_release",
            side_effect=fake_prepare,
        ),
        patch.object(
            release_delivery.pr_delivery,
            "deliver",
            side_effect=fake_deliver,
        ),
    ):
        result = release_delivery.deliver_release_preparation(
            "2.22",
            notes_file=Path("C:/Temp/release-notes-v2.22.md"),
            execute=True,
            authorization="一键准备并交付版本 v2.22",
            timeout=30,
            poll_interval=2,
        )

    assert events == ["prepare", "deliver"]
    assert result["mode"] == "delivered"
    assert result["preparation"] == prepared
    assert result["delivery"] == delivered


def test_preparation_failure_stops_before_push_or_pr_delivery():
    with (
        patch.object(
            release_delivery.release_prepare,
            "prepare_release",
            side_effect=release_delivery.release_prepare.ReleasePreparationError("gate failed"),
        ),
        patch.object(release_delivery.pr_delivery, "deliver") as deliver,
    ):
        with _raises(
            release_delivery.release_prepare.ReleasePreparationError,
            "gate failed",
        ):
            release_delivery.deliver_release_preparation(
                "2.22",
                execute=True,
                authorization="一键准备并交付版本 v2.22",
            )
    deliver.assert_not_called()


def test_already_merged_preparation_is_idempotent_and_does_not_recreate_branch():
    preparation = {"mode": "already_merged", "plan": _plan(state="merged")}
    with (
        patch.object(
            release_delivery.release_prepare,
            "prepare_release",
            return_value=preparation,
        ),
        patch.object(
            release_delivery.release_prepare,
            "_local_branch_exists",
            return_value=False,
        ),
        patch.object(release_delivery.pr_delivery, "deliver") as deliver,
    ):
        result = release_delivery.deliver_release_preparation(
            "2.22",
            execute=True,
            authorization="一键准备并交付版本 v2.22",
        )

    assert result["mode"] == "already_delivered"
    assert result["preparation"] == preparation
    deliver.assert_not_called()


def test_already_merged_preparation_resumes_delivery_cleanup_when_branch_remains():
    preparation = {"mode": "already_merged", "plan": _plan(state="merged")}
    delivery = {"mode": "execute", "merge_sha": "b" * 40}
    with (
        patch.object(
            release_delivery.release_prepare,
            "prepare_release",
            return_value=preparation,
        ),
        patch.object(
            release_delivery.release_prepare,
            "_local_branch_exists",
            return_value=True,
        ),
        patch.object(
            release_delivery.pr_delivery,
            "deliver",
            return_value=delivery,
        ) as deliver,
    ):
        result = release_delivery.deliver_release_preparation(
            "2.22",
            execute=True,
            authorization="一键准备并交付版本 v2.22",
        )

    assert result["mode"] == "delivered"
    assert result["delivery"] == delivery
    deliver.assert_called_once()
