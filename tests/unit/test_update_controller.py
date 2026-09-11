"""Behavioral tests using a controllable clock, IO, and delayed workers."""
import copy
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

import updater
from update_controller import CHECK_INTERVAL, UpdateController, retry_delay
from update_store import UpdateStore


def release(version="2.34"):
    return dict(current="2.33", latest=version, has_update=True, error=None,
                update_type="version", asset_info={"size": 4, "sha256": "a" * 64},
                download_url="https://example.test/app.exe", changelog_body="新版本说明")


class Rig:
    def __init__(self, saved=None):
        self.now = 100000
        self.saved = saved or {}
        self.checks, self.workers = [], []
        self.busy = False
        self.download = Mock(return_value="verified.exe")
        self.controller = UpdateController(
            current_version="2.33", load=lambda: copy.deepcopy(self.saved),
            save=self.save, check=self.checks.append, download=self.download,
            dispatch=lambda callback: callback(), changed=Mock(),
            is_busy=lambda: self.busy, clock=lambda: self.now, spawn=self.workers.append,
        )

    def save(self, snapshot):
        self.saved = copy.deepcopy(snapshot)

    def finish(self, result):
        self.checks.pop(0)(result)


def test_periodic_checks_and_manual_requests_share_one_inflight_request():
    rig = Rig()
    c = rig.controller
    c.tick()
    callbacks = [Mock(), Mock()]
    for callback in callbacks:
        c.request_check(callback)
    assert len(rig.checks) == 1
    rig.finish(release())
    for callback in callbacks:
        callback.assert_called_once()
    assert c.next_check == rig.now + CHECK_INTERVAL
    c.tick()
    assert not rig.checks and not rig.workers  # no default auto-download
    rig.now += CHECK_INTERVAL
    c.tick()
    assert len(rig.checks) == 1


def test_failure_backoff_preserves_reminder_and_success_resets_failures():
    rig = Rig()
    c = rig.controller
    c.request_check()
    rig.finish(release())
    for delay in (900, 1800, 3600, 3600):
        c.request_check()
        rig.finish({"error": "offline", "has_update": False})
        assert c.available["latest"] == "2.34"
        assert c.next_check == rig.now + delay
    c.request_check()
    rig.finish({"error": None, "has_update": False})
    assert c.available is None and c.failures == 0
    assert retry_delay(1) == updater._adaptive_cooldown("failed", 1) == 900


def test_reminder_survives_restart_but_newer_installed_version_clears_it():
    rig = Rig()
    rig.controller.request_check()
    rig.finish(release())
    restored = Rig(rig.saved)
    assert restored.controller.available["changelog_body"] == "新版本说明"
    restored.controller.tick()
    assert not restored.checks
    saved = dict(rig.saved, available=release("2.32"))
    assert Rig(saved).controller.available is None
    saved["available"] = release("2.33")
    assert Rig(saved).controller.available is None


def test_busy_defers_download_and_disabling_does_not_interrupt_existing_transfer():
    rig = Rig()
    c = rig.controller
    c.request_check()
    rig.finish(release())
    rig.busy = True
    c.set_auto_download(True)
    assert not rig.workers
    rig.busy = False
    c.tick()
    assert len(rig.workers) == 1
    c.request_download()
    assert len(rig.workers) == 1
    c.set_auto_download(False)
    rig.workers.pop()()
    assert c.download_state == "ready" and not c.installing
    c.tick()
    assert not rig.workers


def test_download_failure_is_rate_limited_across_restart_but_manual_retry_is_immediate():
    rig = Rig()
    c = rig.controller
    c.request_check()
    rig.finish(release())
    rig.download.side_effect = RuntimeError("offline")
    c.set_auto_download(True)
    rig.workers.pop()()
    assert c.download_state == "failed" and "offline" in c.download_error
    c.tick()
    assert not rig.workers
    restarted = Rig(rig.saved)
    restarted.controller.tick()
    assert not restarted.workers
    assert restarted.controller.request_download()
    assert len(restarted.workers) == 1


def test_new_release_cannot_be_marked_ready_by_old_download_and_close_stops_delivery():
    rig = Rig()
    c = rig.controller
    c.request_check()
    rig.finish(release())
    c.request_download()
    c.request_check()
    rig.finish(release("2.35"))
    rig.workers.pop()()
    assert c.available["latest"] == "2.35" and c.download_state == "idle"
    c.request_download()
    c.close()
    rig.workers.pop()()
    assert c.download_state != "ready"


def test_verified_cache_is_restored_without_network_and_never_installs():
    rig = Rig({"available": release(), "next_check": 101000})
    c = rig.controller
    locate = Mock(return_value="verified.exe")
    c.restore_cache(locate)
    rig.workers.pop()()
    assert c.download_state == "ready" and c.downloaded_path == "verified.exe"
    assert not rig.checks and not c.installing
    assert c.request_download() is False


def test_preference_save_failure_reverts_setting_and_source_disables_automatic_transfer():
    rig = Rig()
    c = rig.controller
    c.save = Mock(side_effect=OSError("read only"))
    try:
        c.set_auto_download(True)
        assert False, "must report persistence failure"
    except OSError:
        pass
    assert not c.auto_download
    c.save = rig.save
    c.automatic_download_supported = False
    c.request_check()
    rig.finish(release())
    c.set_auto_download(True)
    assert not rig.workers


def test_update_snapshot_is_atomic_and_legacy_cooldown_is_migrated():
    with TemporaryDirectory() as folder:
        store = UpdateStore(Path(folder))
        state = {"auto_download": True, "available": release()}
        store.save(state)
        assert store.load() == state
        with patch("update_store.os.replace", side_effect=OSError("locked")):
            try:
                store.save({"auto_download": False})
                assert False
            except OSError:
                pass
        assert store.load() == state
        assert not list(Path(folder).glob(".update-state-*"))
        store.path.unlink()
        (Path(folder) / ".last_update_check").write_text(json.dumps(
            {"timestamp": 1000, "result": "failed", "fail_count": 1},
        ))
        assert store.load()["next_check"] == 1900


def test_downloader_fallback_verifies_and_reuses_cache_without_redownload():
    payload = b"MZtest-install-package"
    info = dict(size=len(payload), sha256=hashlib.sha256(payload).hexdigest())
    target = dict(release(), asset_info=info, download_url_fallback="https://fallback.test/app.exe")
    calls = []
    def download(url, path, progress):
        calls.append(url)
        if "example.test" in url:
            return False, "timeout"
        Path(path).write_bytes(payload)
        return True, None
    with TemporaryDirectory() as folder, patch.object(updater.sys, "platform", "win32"), patch.object(updater, "download_file", download):
        path = updater.download_update_package(target, base_dir=folder)
        assert Path(path).read_bytes() == payload and len(calls) == 2
        assert updater.download_update_package(target, base_dir=folder) == path
        assert len(calls) == 2
        assert not list(Path(folder).rglob("download-*"))


def test_corrupt_download_never_becomes_a_cached_package():
    target = release()
    def download(url, path, progress):
        Path(path).write_bytes(b"MZxx")
        return True, None
    with TemporaryDirectory() as folder, patch.object(updater.sys, "platform", "win32"), patch.object(updater, "download_file", download):
        try:
            updater.download_update_package(target, base_dir=folder)
            assert False
        except RuntimeError as error:
            assert "SHA256" in str(error)
        assert not list(Path(folder).rglob("*.exe"))


def test_macos_package_uses_the_same_verified_persistent_cache():
    payload = b"PKtest-zip-package"
    target = dict(release(), asset_info={"size": len(payload), "sha256": hashlib.sha256(payload).hexdigest()})
    def download(url, path, progress):
        Path(path).write_bytes(payload)
        return True, None
    with TemporaryDirectory() as folder, patch.object(updater.sys, "platform", "darwin"), patch.object(
        updater.sys, "frozen", True, create=True,
    ), patch.object(updater, "download_file", download), patch.object(updater, "update_macos_app") as install:
        path = updater.download_update_package(target, base_dir=folder)
        assert path.endswith("BOSS_ResumeFilter_mac.zip")
        assert str(updater.get_cached_update(target, folder)) == path
        install.assert_not_called()


def test_business_guard_covers_all_active_recruitment_operations():
    from types import SimpleNamespace
    from gui_main import BossFilterGUI
    for name, value in (
        ("is_running", True), ("greet_queue_preparing", True),
        ("greet_queue_running", True), ("_ai_eval_in_progress", True),
        ("_ai_evaluating_ids", {"candidate"}), ("_data_maintenance_running", True),
        ("education_recognition_running", True), ("education_screenshot_running", True),
        ("_external_import_thread", Mock(is_alive=lambda: True)),
        ("_external_import_batch_thread", Mock(is_alive=lambda: True)),
    ):
        assert BossFilterGUI._update_business_busy(SimpleNamespace(**{name: value}))
    for status in ("正在填写", "等待扫码", "待人工验证", "二维码已过期"):
        assert BossFilterGUI._update_business_busy(SimpleNamespace(education_items={"one": {"status": status}}))
    assert not BossFilterGUI._update_business_busy(SimpleNamespace(education_items={"one": {"status": "已保存"}}))


def test_shutdown_cancels_transfer_before_another_request_starts():
    target = release()
    def cancelled(downloaded, total):
        raise InterruptedError("closed")
    with TemporaryDirectory() as folder, patch.object(updater.sys, "platform", "win32"), patch.object(updater, "download_file") as download:
        try:
            updater.download_update_package(target, cancelled, base_dir=folder)
            assert False
        except InterruptedError:
            pass
        download.assert_not_called()
        assert not list(Path(folder).rglob("download-*"))
