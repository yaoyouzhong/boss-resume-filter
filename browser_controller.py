"""Chrome lifecycle, connection, and debounce control without Tk dependencies."""
from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class BrowserRuntime:
    """Explicit operating-system and browser connection dependencies."""

    platform: str
    environ: Mapping[str, str]
    exists: Callable[[str], bool]
    expandvars: Callable[[str], str]
    expanduser: Callable[[str], str]
    which: Callable[[str], str | None]
    socket_factory: Callable[..., Any]
    popen: Callable[..., Any]
    run_process: Callable[..., Any]
    kill_process: Callable[[int, int], Any]
    sleep: Callable[[float], None]
    port_open: Callable[..., bool]
    connector: Callable[..., Any]


@dataclass(frozen=True)
class BrowserConnectionState:
    """Plain browser connection state returned to the GUI host."""

    connected: bool
    page: Any = None
    address: str = ""
    url: str = ""
    status: str = "disconnected"
    error: str = ""


@dataclass(frozen=True)
class ProcessCleanupResult:
    killed: bool
    error: str = ""


class BrowserController:
    """Own managed Chrome lifecycle and transient connection counters."""

    def __init__(
        self,
        base_dir: Path,
        port_file: Path,
        runtime: BrowserRuntime,
    ) -> None:
        self._base_dir = base_dir
        self._port_file = port_file
        self._runtime = runtime
        self._non_target_checks = 0
        self._connection_failures = 0

    @staticmethod
    def is_page_alive(page: Any) -> bool:
        if page is None:
            return False
        try:
            page.run_js("return 1")
            return True
        except Exception:
            return False

    def should_defer_navigation(self, silent: bool) -> bool:
        self._non_target_checks += 1
        return silent and self._non_target_checks < 2

    def reset_navigation_failures(self) -> None:
        self._non_target_checks = 0

    def should_defer_connection_failure(self, silent: bool) -> bool:
        self._connection_failures += 1
        return silent and self._connection_failures < 2

    def reset_connection_failures(self) -> None:
        self._connection_failures = 0

    def saved_port(self) -> str:
        try:
            port = self._port_file.read_text(encoding="utf-8").strip()
        except OSError:
            return ""
        return port if port.isdigit() else ""

    def address_candidates(self, current_address: object) -> tuple[str, ...]:
        candidates: list[str] = []
        current = str(current_address or "").strip()
        if current:
            candidates.append(current)
        saved_port = self.saved_port()
        if saved_port:
            candidates.append(f"127.0.0.1:{saved_port}")
        candidates.append("127.0.0.1:9222")
        return tuple(dict.fromkeys(candidates))

    def reconnect(
        self,
        current_address: object,
        *,
        timeout: float = 4,
        prefer_boss_tab: bool = True,
        validate_page: bool = True,
    ) -> BrowserConnectionState:
        """Reconnect to the first live explicit, persisted, or default port."""
        for address in self.address_candidates(current_address):
            if not self._runtime.port_open(address, timeout=0.5):
                continue
            connection = self._runtime.connector(
                address,
                timeout=timeout,
                prefer_boss_tab=prefer_boss_tab,
                validate_page=validate_page,
            )
            if getattr(connection, "timed_out", False):
                return BrowserConnectionState(
                    False,
                    address=address,
                    status="timeout",
                    error="浏览器页面连接超时",
                )
            page = getattr(connection, "page", None)
            if not self.is_page_alive(page):
                continue
            return BrowserConnectionState(
                True,
                page=page,
                address=str(getattr(connection, "address", "") or address),
                url=str(getattr(connection, "url", "") or getattr(page, "url", "") or ""),
                status="connected",
            )
        return BrowserConnectionState(False, status="not_found")

    def launch_managed_chrome(
        self,
        target_url: str,
        *,
        recommend_matcher: Callable[[str], bool],
        wait_attempts: int = 40,
        should_stop: Callable[[], bool] = lambda: False,
    ) -> BrowserConnectionState:
        """Start the managed profile, wait for its port, connect, and navigate."""
        chrome_path = self._find_chrome_path()
        if not chrome_path:
            return BrowserConnectionState(
                False,
                status="chrome_missing",
                error="未找到 Chrome 浏览器，请安装后重试。",
            )
        try:
            debug_port = self._allocate_port()
            profile_dir = self._base_dir / ".chrome_profile"
            profile_dir.mkdir(parents=True, exist_ok=True)
            self._clear_profile_locks(profile_dir)
            self._runtime.popen(
                [
                    chrome_path,
                    f"--remote-debugging-port={debug_port}",
                    f"--user-data-dir={profile_dir}",
                    "--no-first-run",
                    "--no-default-browser-check",
                    target_url,
                ],
                stdout=-3,
                stderr=-3,
                show_window=True,
            )
            try:
                self._port_file.write_text(str(debug_port), encoding="utf-8")
            except OSError:
                pass
        except (OSError, TypeError, ValueError) as exc:
            return BrowserConnectionState(
                False,
                status="launch_error",
                error=f"Chrome 启动失败：{str(exc)[:120]}",
            )

        address = f"127.0.0.1:{debug_port}"
        for _ in range(max(1, wait_attempts)):
            if should_stop():
                return BrowserConnectionState(
                    False,
                    address=address,
                    status="stopped",
                    error="发送已停止。",
                )
            if self._runtime.port_open(address, timeout=0.5):
                break
            self._runtime.sleep(0.5)
        else:
            return BrowserConnectionState(
                False,
                address=address,
                status="launch_timeout",
                error="Chrome 启动超时，请关闭应用专用 Chrome 后重试。",
            )

        try:
            connection = self._runtime.connector(
                address,
                timeout=6,
                prefer_boss_tab=True,
                validate_page=True,
            )
        except Exception as exc:
            return BrowserConnectionState(
                False,
                address=address,
                status="connect_error",
                error=f"Chrome 已启动，但程序无法连接页面：{str(exc)[:120]}",
            )
        page = getattr(connection, "page", None)
        if (
            getattr(connection, "timed_out", False)
            or getattr(connection, "error", None) is not None
            or not self.is_page_alive(page)
        ):
            return BrowserConnectionState(
                False,
                address=address,
                status="connect_error",
                error="Chrome 已启动，但程序无法连接页面，请稍后重试。",
            )
        try:
            current_url = str(getattr(page, "url", "") or "")
            if not recommend_matcher(current_url):
                page.get(target_url)
                current_url = str(getattr(page, "url", "") or "")
        except Exception as exc:
            return BrowserConnectionState(
                False,
                page=page,
                address=address,
                status="navigation_error",
                error=f"Chrome 已启动，但推荐牛人页面打开失败：{str(exc)[:120]}",
            )
        return BrowserConnectionState(
            recommend_matcher(current_url),
            page=page,
            address=str(getattr(connection, "address", "") or address),
            url=current_url,
            status=("recommend" if recommend_matcher(current_url) else "non_target"),
            error=("" if recommend_matcher(current_url) else "Chrome 已启动，但推荐牛人页面未能打开。"),
        )

    def terminate_debug_processes(self, port: int) -> ProcessCleanupResult:
        """Terminate only Chrome processes explicitly bound to one debug port."""
        runtime = self._runtime
        killed = False
        try:
            if runtime.platform == "darwin" or runtime.platform.startswith("linux"):
                result = runtime.run_process(
                    ["pgrep", "-f", f"remote-debugging-port={port}"],
                    capture_output=True,
                    text=True,
                    timeout=3,
                )
                for value in str(result.stdout or "").splitlines():
                    if not value.strip().isdigit():
                        continue
                    try:
                        runtime.kill_process(int(value.strip()), 15)
                        killed = True
                    except ProcessLookupError:
                        pass
            elif runtime.platform == "win32":
                result = runtime.run_process(
                    [
                        "wmic",
                        "process",
                        "where",
                        f"CommandLine like '%remote-debugging-port={port}%'",
                        "get",
                        "ProcessId",
                    ],
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                for value in str(result.stdout or "").splitlines():
                    if not value.strip().isdigit():
                        continue
                    runtime.run_process(
                        ["taskkill", "/PID", value.strip()],
                        timeout=2,
                        stdout=-3,
                        stderr=-3,
                    )
                    killed = True
        except Exception as exc:
            return ProcessCleanupResult(killed, str(exc))
        return ProcessCleanupResult(killed)

    def _find_chrome_path(self) -> str | None:
        runtime = self._runtime
        if runtime.platform == "darwin":
            candidates = (
                "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                runtime.expanduser(
                    "~/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
                ),
            )
        elif runtime.platform == "win32":
            candidates = (
                runtime.expandvars(
                    r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"
                ),
                r"C:\Program Files\Google\Chrome\Application\chrome.exe",
                r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
            )
        else:
            candidates = (
                runtime.which("google-chrome"),
                runtime.which("google-chrome-stable"),
                runtime.which("chromium"),
            )
        return next(
            (str(path) for path in candidates if path and runtime.exists(str(path))),
            None,
        )

    def _allocate_port(self) -> int:
        with self._runtime.socket_factory() as port_socket:
            port_socket.bind(("127.0.0.1", 0))
            return int(port_socket.getsockname()[1])

    @staticmethod
    def _clear_profile_locks(profile_dir: Path) -> None:
        for name in ("SingletonLock", "SingletonSocket", "SingletonCookie"):
            try:
                (profile_dir / name).unlink(missing_ok=True)
            except OSError:
                pass
