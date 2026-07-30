import contextlib
import io
import os
import re
import subprocess
import sys
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parents[2]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from subprocess_utils import HiddenSubprocess  # noqa: E402


class _FakeStartupInfo:
    def __init__(self):
        self.dwFlags = 0
        self.wShowWindow = None


class _FakeSubprocess:
    CREATE_NO_WINDOW = 0x08000000
    STARTF_USESHOWWINDOW = 0x00000001
    SW_HIDE = 0
    PIPE = subprocess.PIPE
    CalledProcessError = subprocess.CalledProcessError

    def __init__(self):
        self.calls = []

    @staticmethod
    def STARTUPINFO():  # noqa: N802
        return _FakeStartupInfo()

    def run(self, *args, **kwargs):
        self.calls.append(("run", args, kwargs))
        return subprocess.CompletedProcess(
            args[0],
            0,
            stdout="command output\n" if "stdout" in kwargs else None,
            stderr="",
        )

    def Popen(self, *args, **kwargs):  # noqa: N802
        self.calls.append(("popen", args, kwargs))
        return "popen-result"


def test_hidden_subprocess_hides_windows_commands_and_disables_gh_helpers():
    fake = _FakeSubprocess()
    proxy = HiddenSubprocess(fake, platform="win32", has_console=False)

    result = proxy.run(["gh", "pr", "view", "49"], capture_output=True)

    assert result.returncode == 0
    _kind, _args, kwargs = fake.calls[0]
    assert kwargs["creationflags"] & fake.CREATE_NO_WINDOW
    assert kwargs["startupinfo"].dwFlags & fake.STARTF_USESHOWWINDOW
    assert kwargs["startupinfo"].wShowWindow == fake.SW_HIDE
    assert kwargs["env"]["CODEX_GH_SHIM_FORCE_CODEX"] == "1"
    assert kwargs["env"]["GH_TELEMETRY"] == "false"
    assert kwargs["env"]["TZ"] == "Asia/Shanghai"


def test_hidden_subprocess_hides_windows_popen_without_changing_normal_env():
    fake = _FakeSubprocess()
    proxy = HiddenSubprocess(fake, platform="win32", has_console=False)
    original_env = {"CUSTOM": "1"}

    result = proxy.Popen(["git", "status"], env=original_env)

    assert result == "popen-result"
    _kind, _args, kwargs = fake.calls[0]
    assert kwargs["creationflags"] & fake.CREATE_NO_WINDOW
    assert kwargs["startupinfo"].dwFlags & fake.STARTF_USESHOWWINDOW
    assert kwargs["env"] is original_env


def test_visible_gui_process_can_explicitly_keep_its_window():
    fake = _FakeSubprocess()
    proxy = HiddenSubprocess(fake, platform="win32", has_console=False)

    proxy.Popen(["chrome.exe", "https://example.com"], show_window=True)

    _kind, _args, kwargs = fake.calls[0]
    assert "creationflags" not in kwargs
    assert "startupinfo" not in kwargs
    assert "show_window" not in kwargs


def test_hidden_subprocess_preserves_non_windows_launch_kwargs():
    fake = _FakeSubprocess()
    proxy = HiddenSubprocess(fake, platform="linux")

    proxy.run(["gh", "--version"], env={"CUSTOM": "1"})

    _kind, _args, kwargs = fake.calls[0]
    assert kwargs == {"env": {"CUSTOM": "1"}}


def test_hidden_subprocess_hides_normal_windows_commands():
    fake = _FakeSubprocess()
    proxy = HiddenSubprocess(fake, platform="win32", has_console=False)
    original_env = {"CUSTOM": "1"}

    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        proxy.run(["git", "status"], env=original_env, text=True)

    _kind, _args, kwargs = fake.calls[0]
    assert kwargs["creationflags"] & fake.CREATE_NO_WINDOW
    assert kwargs["startupinfo"].dwFlags & fake.STARTF_USESHOWWINDOW
    assert kwargs["env"] is original_env
    assert kwargs["stdout"] == fake.PIPE
    assert kwargs["stderr"] == fake.PIPE
    assert output.getvalue() == "command output\n"


def test_hidden_subprocess_relays_uncaptured_github_output():
    fake = _FakeSubprocess()
    proxy = HiddenSubprocess(fake, platform="win32", has_console=False)
    output = io.StringIO()

    with contextlib.redirect_stdout(output):
        result = proxy.run(["gh", "--version"], text=True)

    assert result.returncode == 0
    assert output.getvalue() == "command output\n"
    _kind, _args, kwargs = fake.calls[0]
    assert kwargs["stdout"] == fake.PIPE
    assert kwargs["stderr"] == fake.PIPE


def test_console_parent_reuses_existing_console_for_whole_process_tree():
    fake = _FakeSubprocess()
    proxy = HiddenSubprocess(fake, platform="win32", has_console=True)

    proxy.run(["gh", "pr", "view", "50"], capture_output=True)

    _kind, _args, kwargs = fake.calls[0]
    assert "creationflags" not in kwargs
    assert "startupinfo" not in kwargs
    assert kwargs["env"]["CODEX_GH_SHIM_FORCE_CODEX"] == "1"
    assert kwargs["env"]["GH_TELEMETRY"] == "false"
    assert kwargs["env"]["TZ"] == "Asia/Shanghai"


def test_release_entrypoints_use_hidden_subprocess_proxy():
    import build
    import pr_delivery
    import product_fingerprint
    import release_ci
    import release_dispatch
    import release_flow
    import release_prepare

    modules = (
        build,
        pr_delivery,
        product_fingerprint,
        release_ci,
        release_dispatch,
        release_flow,
        release_prepare,
    )
    assert all(isinstance(module.subprocess, HiddenSubprocess) for module in modules)


def test_runtime_and_build_entrypoints_use_hidden_subprocess_proxy():
    import build_education_tool
    import gui_main
    import updater

    modules = (build_education_tool, gui_main, updater)
    assert all(isinstance(module.subprocess, HiddenSubprocess) for module in modules)


def test_production_subprocess_calls_cannot_bypass_hidden_proxy():
    call_pattern = re.compile(
        r"\bsubprocess\.(?:run|Popen|call|check_call|check_output)\s*\("
    )
    uncovered = []
    production_paths = [
        *BASE_DIR.glob("*.py"),
        *(BASE_DIR / "scripts").glob("*.py"),
        *(BASE_DIR / "docs").glob("*.py"),
    ]
    for path in production_paths:
        relative = path.relative_to(BASE_DIR)
        if path.name == "subprocess_utils.py":
            continue
        source = path.read_text(encoding="utf-8")
        if call_pattern.search(source) and "hidden_subprocess" not in source:
            uncovered.append(relative.as_posix())

    assert uncovered == [], f"subprocess calls bypass hidden proxy: {uncovered}"


def test_real_windows_child_process_runs_hidden_and_preserves_output():
    if sys.platform != "win32":
        return

    proxy = HiddenSubprocess(subprocess)
    result = proxy.run(
        [sys.executable, "-c", "print('hidden-child-ok')"],
        capture_output=True,
        text=True,
        check=True,
    )

    assert result.stdout.strip() == "hidden-child-ok"


def test_real_windows_descendant_inherits_existing_console():
    if sys.platform != "win32":
        return

    proxy = HiddenSubprocess(subprocess)
    parent_code = (
        "import subprocess,sys;"
        "subprocess.run([sys.executable,'-c',"
        "\"print('hidden-descendant-ok')\"],check=True)"
    )
    result = proxy.run(
        [sys.executable, "-c", parent_code],
        capture_output=True,
        text=True,
        check=True,
    )

    assert result.stdout.strip() == "hidden-descendant-ok"


def test_pr_delivery_direct_entrypoint_can_import_hidden_proxy():
    proxy = HiddenSubprocess(subprocess)
    result = proxy.run(
        [
            sys.executable,
            str(BASE_DIR / "scripts" / "pr_delivery.py"),
            "--help",
        ],
        cwd=BASE_DIR,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )

    assert result.returncode == 0, result.stderr
    assert "--authorization" in result.stdout


def test_github_cli_environment_keeps_existing_explicit_values():
    fake = _FakeSubprocess()
    proxy = HiddenSubprocess(fake, platform="win32", has_console=False)
    explicit_env = {
        **os.environ,
        "CODEX_GH_SHIM_FORCE_CODEX": "custom",
        "GH_TELEMETRY": "custom",
        "TZ": "UTC",
    }

    proxy.run(
        ["gh.exe", "auth", "status"],
        env=explicit_env,
        capture_output=True,
    )

    _kind, _args, kwargs = fake.calls[0]
    assert kwargs["env"]["CODEX_GH_SHIM_FORCE_CODEX"] == "custom"
    assert kwargs["env"]["GH_TELEMETRY"] == "custom"
    assert kwargs["env"]["TZ"] == "UTC"
