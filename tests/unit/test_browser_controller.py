import tempfile
from pathlib import Path
from types import SimpleNamespace

from browser_controller import BrowserController, BrowserRuntime


class _Socket:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def bind(self, _address):
        return None

    def getsockname(self):
        return "127.0.0.1", 32123


class _Page:
    def __init__(self, url="https://www.zhipin.com/web/chat/recommend"):
        self.url = url

    def run_js(self, _script):
        return 1

    def get(self, url):
        self.url = url


def _runtime(**overrides):
    values = {
        "platform": "win32",
        "environ": {},
        "exists": lambda _path: True,
        "expandvars": lambda path: path,
        "expanduser": lambda path: path,
        "which": lambda _name: None,
        "socket_factory": _Socket,
        "popen": lambda *_args, **_kwargs: None,
        "run_process": lambda *_args, **_kwargs: SimpleNamespace(stdout=""),
        "kill_process": lambda *_args: None,
        "sleep": lambda _seconds: None,
        "port_open": lambda *_args, **_kwargs: True,
        "connector": lambda address, **_kwargs: SimpleNamespace(
            timed_out=False,
            page=_Page(),
            address=address,
            url="https://www.zhipin.com/web/chat/recommend",
        ),
    }
    values.update(overrides)
    return BrowserRuntime(**values)


def _controller(runtime=None):
    temp_dir = tempfile.TemporaryDirectory()
    root = Path(temp_dir.name)
    return temp_dir, BrowserController(root, root / ".chrome_debug_port", runtime or _runtime())


def test_address_candidates_prefer_explicit_then_saved_then_default():
    temp_dir, controller = _controller()
    try:
        controller._port_file.write_text("9333", encoding="utf-8")
        addresses = controller.address_candidates("127.0.0.1:9444")
    finally:
        temp_dir.cleanup()

    assert addresses == (
        "127.0.0.1:9444",
        "127.0.0.1:9333",
        "127.0.0.1:9222",
    )


def test_navigation_and_connection_failures_debounce_once():
    temp_dir, controller = _controller()
    try:
        assert controller.should_defer_navigation(True) is True
        assert controller.should_defer_navigation(True) is False
        controller.reset_navigation_failures()
        assert controller.should_defer_navigation(False) is False
        assert controller.should_defer_connection_failure(True) is True
        assert controller.should_defer_connection_failure(True) is False
    finally:
        temp_dir.cleanup()


def test_reconnect_skips_closed_ports_and_returns_first_live_page():
    attempts = []

    def port_open(address, **_kwargs):
        return address.endswith("9222")

    def connector(address, **_kwargs):
        attempts.append(address)
        return SimpleNamespace(
            timed_out=False,
            page=_Page(),
            address=address,
            url="recommend",
        )

    temp_dir, controller = _controller(_runtime(port_open=port_open, connector=connector))
    try:
        state = controller.reconnect("127.0.0.1:9444")
    finally:
        temp_dir.cleanup()

    assert state.connected is True
    assert state.address == "127.0.0.1:9222"
    assert attempts == ["127.0.0.1:9222"]


def test_launch_reports_missing_chrome_without_starting_process():
    popen_calls = []
    temp_dir, controller = _controller(_runtime(
        exists=lambda _path: False,
        popen=lambda *_args, **_kwargs: popen_calls.append(True),
    ))
    try:
        state = controller.launch_managed_chrome(
            "https://www.zhipin.com/web/chat/recommend",
            recommend_matcher=lambda url: "recommend" in url,
        )
    finally:
        temp_dir.cleanup()

    assert state.status == "chrome_missing"
    assert popen_calls == []


def test_launch_uses_managed_profile_and_returns_connected_page():
    launched = []
    temp_dir, controller = _controller(_runtime(
        popen=lambda args, **kwargs: launched.append((args, kwargs)),
    ))
    try:
        state = controller.launch_managed_chrome(
            "https://www.zhipin.com/web/chat/recommend",
            recommend_matcher=lambda url: "recommend" in url,
        )
        root = controller._base_dir
        stored_port = controller._port_file.read_text(encoding="utf-8")
    finally:
        temp_dir.cleanup()

    assert state.connected is True
    assert stored_port == "32123"
    assert f"--user-data-dir={root / '.chrome_profile'}" in launched[0][0]
    assert launched[0][1]["show_window"] is True


def test_launch_wait_honors_stop_signal_before_reconnect():
    connector_calls = []
    temp_dir, controller = _controller(_runtime(
        port_open=lambda *_args, **_kwargs: False,
        connector=lambda *_args, **_kwargs: connector_calls.append(True),
    ))
    try:
        state = controller.launch_managed_chrome(
            "https://www.zhipin.com/web/chat/recommend",
            recommend_matcher=lambda url: "recommend" in url,
            should_stop=lambda: True,
        )
    finally:
        temp_dir.cleanup()

    assert state.status == "stopped"
    assert connector_calls == []


def test_windows_process_cleanup_targets_only_requested_debug_port():
    commands = []

    def run_process(args, **_kwargs):
        commands.append(args)
        return SimpleNamespace(stdout="ProcessId\n1234\n")

    temp_dir, controller = _controller(_runtime(run_process=run_process))
    try:
        result = controller.terminate_debug_processes(9333)
    finally:
        temp_dir.cleanup()

    assert result.killed is True
    assert any("remote-debugging-port=9333" in value for value in commands[0])
    assert commands[1] == ["taskkill", "/PID", "1234"]
