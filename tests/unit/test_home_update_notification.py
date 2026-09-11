"""Update detection must not interrupt the home workbench."""
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

import updater


class ImmediateThread:
    def __init__(self, target, daemon=False):
        self.target = target

    def start(self):
        self.target()


class ImmediateRoot:
    def after(self, delay, callback):
        callback()


def test_available_update_is_delivered_without_dialog_and_manual_still_opens():
    result = {"current": "2.33", "latest": "2.34", "has_update": True, "error": None}
    notify, completed = Mock(), Mock()
    gui = Mock()
    gui.run_on_ui.side_effect = lambda callback: callback()
    with (
        patch.object(updater.threading, "Thread", ImmediateThread),
        patch.object(updater, "check_gitee_latest", return_value=result),
        patch.object(updater, "_fetch_changelog_section", return_value="升级内容"),
        patch.object(updater, "_get_cached_windows_update", return_value=None),
        patch.object(updater, "show_update_dialog") as dialog,
    ):
        updater.check_and_update_gui(
            ImmediateRoot(), silent=True, source="startup", gui=gui,
            on_update_available=notify, on_complete=completed,
        )
        dialog.assert_not_called()
        notify.assert_called_once_with(result)
        completed.assert_called_once_with(result)
        assert result["changelog_body"] == "升级内容"
        updater.check_and_update_gui(ImmediateRoot(), source="manual")
        dialog.assert_called_once()


def test_home_notification_rechecks_found_version_during_legacy_defer_cooldown():
    with TemporaryDirectory() as directory, patch.object(
        updater, "get_base_dir", return_value=Path(directory),
    ), patch.object(updater, "check_and_update_gui") as check:
        updater._write_update_defer_cooldown(Path(directory))
        notify = Mock()
        updater.auto_check_on_startup(ImmediateRoot(), on_update_available=notify)
        assert check.call_args.kwargs["on_update_available"] is notify
        check.reset_mock()
        updater.auto_check_on_startup(ImmediateRoot())
        check.assert_not_called()
        for status in ("no_update", "failed"):
            updater._write_cooldown(Path(directory), status)
            updater.auto_check_on_startup(ImmediateRoot(), on_update_available=notify)
            check.assert_not_called()
