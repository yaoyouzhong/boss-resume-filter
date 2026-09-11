"""Update scheduling and shared download lifecycle, independent of Tk windows."""
from __future__ import annotations

import copy
import math
import threading
import time
from collections.abc import Callable
from typing import Any


CHECK_INTERVAL = 4 * 3600


def retry_delay(failures: int) -> int:
    """First failure waits 15 minutes, then 30 minutes, then at most one hour."""
    return 900 * 2 ** min(max(failures - 1, 0), 2)


def update_identity(result: dict | None) -> tuple[str, str]:
    result = result or {}
    return str(result.get("latest", "")), str((result.get("asset_info") or {}).get("sha256", ""))


def _version(value: Any) -> tuple[int, ...]:
    try:
        parts = str(value).removeprefix("v").split(".")
        if len(parts) not in (2, 3) or not all(part.isdigit() for part in parts):
            return ()
        return tuple(int(part) for part in parts) + (0,) * (3 - len(parts))
    except (TypeError, ValueError):
        return ()


def _number(value: Any, default: float = 0) -> float:
    try:
        number = float(value)
        return number if math.isfinite(number) and number >= 0 else default
    except (ValueError, TypeError):
        return default


class UpdateController:
    """UI-thread orchestration; worker results re-enter through injected dispatch.

    Check and download callbacks perform all external IO. Closing a dialog never
    closes this controller. The application owns its lifetime and calls tick.
    """

    def __init__(
        self, *, current_version: str, load: Callable, save: Callable,
        check: Callable, download: Callable, dispatch: Callable,
        changed: Callable, is_busy: Callable[[], bool],
        automatic_download_supported: bool = True,
        clock: Callable[[], float] = time.time,
        spawn: Callable | None = None,
    ) -> None:
        self.current_version = current_version
        self.save = save
        self.check = check
        self.download = download
        self.dispatch = dispatch
        self.changed = changed
        self.is_busy = is_busy
        self.automatic_download_supported = automatic_download_supported
        self.clock = clock
        self.spawn = spawn or (lambda fn: threading.Thread(target=fn, daemon=True).start())
        state = load()
        self.auto_download = state.get("auto_download") is True
        self.next_check = min(_number(state.get("next_check")), clock() + CHECK_INTERVAL)
        self.failures = int(min(_number(state.get("failures")), 100))
        self.available: dict | None = None
        saved = state.get("available")
        if isinstance(saved, dict) and isinstance(saved.get("asset_info", {}), dict):
            # Only restore a newer version, or content updates for this exact build.
            newer = _version(saved.get("latest")) > _version(current_version)
            same_content = (
                _version(saved.get("latest")) == _version(current_version)
                and saved.get("current") == current_version
                and saved.get("update_type") == "content"
            )
            if _version(saved.get("latest")) and saved.get("has_update") and (newer or same_content):
                self.available = dict(saved, current=current_version)
                self.available.pop("cached_update_path", None)
        self.download_retry_at = min(_number(state.get("download_retry_at")), clock() + CHECK_INTERVAL)
        self.download_attempt = tuple(state.get("download_attempt", ())) if isinstance(state.get("download_attempt"), list) else ()
        self.download_state = "idle"
        self.download_error = ""
        self.downloaded_path: str | None = None
        self.download_key: tuple[str, str] = ("", "")
        self.progress = (0, 0)
        self.checking = False
        self.installing = False
        self.closed = False
        self.storage_error = ""
        self._waiters: list[Callable] = []
        self._cancel = threading.Event()

    def restore_cache(self, locate: Callable) -> None:
        """Verify a persisted reminder's package in a worker, without a network request."""
        if not self.available:
            return
        result = copy.deepcopy(self.available)
        key = update_identity(result)

        def work() -> None:
            try:
                path = locate(result)
            except (OSError, ValueError):
                path = None
            def finish() -> None:
                if (not self.closed and path and key == update_identity(self.available)
                        and self.download_state != "downloading"):
                    self.download_key, self.downloaded_path, self.download_state = key, str(path), "ready"
                    self.changed()
            if not self.closed:
                self.dispatch(finish)
        self.spawn(work)

    def _save(self, *, strict: bool = False) -> None:
        available = copy.deepcopy(self.available)
        if available:
            available.pop("cached_update_path", None)
        try:
            self.save(dict(
                auto_download=self.auto_download, next_check=self.next_check,
                failures=self.failures, available=available,
                download_attempt=list(self.download_attempt), download_retry_at=self.download_retry_at,
            ))
            self.storage_error = ""
        except OSError as error:
            self.storage_error = f"更新偏好或提醒未能保存：{error}"
            if strict:
                raise

    def set_auto_download(self, enabled: bool) -> None:
        previous = self.auto_download
        self.auto_download = bool(enabled)
        try:
            self._save(strict=True)
        except OSError:
            self.auto_download = previous
            raise
        # Disabling affects subsequent downloads; an existing transfer completes.
        self.changed()
        self.tick()

    def tick(self) -> None:
        """Called periodically by the host, including after resume from sleep."""
        if self.closed or self.installing:
            return
        if self.clock() >= self.next_check and not self.checking:
            self.request_check()
        if (self.auto_download and self.automatic_download_supported and self.available
                and not self.checking and not self.is_busy()):
            self.request_download(automatic=True)

    def request_check(self, completed: Callable | None = None) -> None:
        """Manual requests share the in-flight check and bypass only the timer."""
        if self.closed or self.installing:
            return
        if completed:
            self._waiters.append(completed)
        if self.checking:
            return
        self.checking = True
        self.changed()
        try:
            self.check(self._checked)
        except Exception as error:
            self._checked({"error": str(error), "has_update": False})

    def _checked(self, result: dict) -> None:
        if self.closed:
            return
        self.checking = False
        if result.get("error"):
            self.failures += 1
            self.next_check = self.clock() + retry_delay(self.failures)
        else:
            self.failures = 0
            self.next_check = self.clock() + CHECK_INTERVAL
            previous = update_identity(self.available)
            self.available = copy.deepcopy(result) if result.get("has_update") else None
            if update_identity(self.available) != previous and self.download_state != "downloading":
                self.download_state, self.downloaded_path, self.download_error = "idle", None, ""
            cached = result.get("cached_update_path")
            if self.available and cached and self.download_state != "downloading":
                self.download_key = update_identity(self.available)
                self.download_state, self.downloaded_path = "ready", str(cached)
            elif self.download_state == "ready" and not cached:
                self.download_state, self.downloaded_path = "idle", None
        self._save()
        self.changed()
        waiters, self._waiters = self._waiters, []
        for callback in waiters:
            callback(result)

    def request_download(self, *, automatic: bool = False) -> bool:
        """One shared transfer. Automatic failures are retried at most every four hours."""
        if self.closed or self.installing or not self.available or self.download_state == "downloading":
            return False
        key = update_identity(self.available)
        if key == self.download_key and self.download_state == "ready":
            return False
        if automatic and (not self.automatic_download_supported or self.is_busy()
                          or (key == self.download_attempt and self.clock() < self.download_retry_at)):
            return False
        result = copy.deepcopy(self.available)
        self.download_key = self.download_attempt = key
        self.download_retry_at = self.clock() + CHECK_INTERVAL
        self.download_state, self.download_error, self.progress = "downloading", "", (0, 0)
        self._save()
        self.changed()
        self._cancel.clear()

        def progress(downloaded: int, total: int) -> None:
            if self._cancel.is_set():
                raise InterruptedError("应用已退出，下载已停止")
            # A lock-free immutable pair; the UI reads it at its own bounded rate.
            self.progress = downloaded, total

        def work() -> None:
            path, error = None, ""
            try:
                path = self.download(result, progress)
            except Exception as failure:
                error = str(failure)
            if not self.closed:
                self.dispatch(lambda: self._downloaded(key, path, error))

        self.spawn(work)
        return True

    def _downloaded(self, key: tuple[str, str], path: str | None, error: str) -> None:
        if self.closed:
            return
        if key != update_identity(self.available):
            self.download_state, self.downloaded_path, self.download_error = "idle", None, ""
        else:
            self.download_state = "ready" if path else "failed"
            self.downloaded_path = str(path) if path else None
            self.download_error = error
        self.changed()

    def close(self) -> None:
        self.closed = True
        self._cancel.set()
        self._waiters.clear()

    def tooltip(self) -> str:
        if self.download_state == "downloading":
            return "正在下载更新，点击查看进度"
        if self.download_state == "ready":
            return "更新已就绪，点击查看"
        if self.download_state == "failed":
            return "更新下载未完成，点击重试"
        return "有新版本，点击查看升级内容"
