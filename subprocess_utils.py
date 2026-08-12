"""Subprocess helpers that keep project process trees hidden on Windows."""
from __future__ import annotations

import os
import sys
import ctypes
from pathlib import Path
from types import ModuleType
from typing import Any, TextIO


def _is_github_cli(command: Any) -> bool:
    """Return whether a subprocess command directly launches GitHub CLI."""
    if isinstance(command, (list, tuple)) and command:
        executable = str(command[0])
    elif isinstance(command, str):
        executable = command.strip().split(maxsplit=1)[0] if command.strip() else ""
    else:
        return False
    return Path(executable.strip('"')).name.lower() in {"gh", "gh.exe"}


def _is_gitee_git_command(command: Any) -> bool:
    """Return whether a Git subprocess communicates with the Gitee remote."""
    if not isinstance(command, (list, tuple)) or not command:
        return False
    if Path(str(command[0]).strip('"')).name.lower() not in {"git", "git.exe"}:
        return False
    return any(
        str(argument).lower() == "gitee"
        or "gitee.com/" in str(argument).lower()
        for argument in command[1:]
    )


def _gitee_direct_environment(source: dict[str, str] | None = None) -> dict[str, str]:
    """Return an environment that bypasses proxies only for Gitee hosts."""
    env = dict(source or os.environ)
    existing = env.get("NO_PROXY") or env.get("no_proxy") or ""
    entries = [item.strip() for item in existing.split(",") if item.strip()]
    lowered = {item.lower() for item in entries}
    for host in ("gitee.com", ".gitee.com"):
        if host.lower() not in lowered:
            entries.append(host)
            lowered.add(host.lower())
    no_proxy = ",".join(entries)
    env["NO_PROXY"] = no_proxy
    env["no_proxy"] = no_proxy
    return env


def _has_attached_console(platform: str) -> bool:
    """Return whether the current Windows process already owns a console."""
    if platform != "win32":
        return False
    try:
        return bool(ctypes.windll.kernel32.GetConsoleWindow())
    except (AttributeError, OSError):
        return False


class HiddenSubprocess:
    """Proxy ``subprocess`` while preventing child console-window activation."""

    def __init__(
        self,
        module: ModuleType,
        *,
        platform: str | None = None,
        has_console: bool | None = None,
    ) -> None:
        self._module = module
        self._platform = platform or sys.platform
        self._has_console = (
            _has_attached_console(self._platform)
            if has_console is None
            else has_console
        )

    def __getattr__(self, name: str) -> Any:
        return getattr(self._module, name)

    def _prepare_kwargs(self, command: Any, kwargs: dict[str, Any]) -> dict[str, Any]:
        prepared = dict(kwargs)
        show_window = bool(prepared.pop("show_window", False))
        if _is_gitee_git_command(command):
            prepared["env"] = _gitee_direct_environment(prepared.get("env"))
        if self._platform != "win32":
            return prepared

        if _is_github_cli(command):
            env = dict(prepared.get("env") or os.environ)
            # The locally deployed GUI-subsystem gh compatibility shim only
            # hides the real CLI automatically when ChatGPT is its direct
            # parent. Release commands are launched by Python, so opt into the
            # shim's hidden mode explicitly.
            env.setdefault("CODEX_GH_SHIM_FORCE_CODEX", "1")
            env.setdefault("GH_TELEMETRY", "false")
            env.setdefault("TZ", "Asia/Shanghai")
            prepared["env"] = env

        if show_window or self._has_console:
            # Reuse the caller's console so Git/gh helper processes inherit it.
            # Creating a separate "hidden" console still activates Windows
            # Terminal on systems that use it as the default console host.
            return prepared

        create_no_window = int(getattr(self._module, "CREATE_NO_WINDOW", 0))
        if create_no_window:
            prepared["creationflags"] = (
                int(prepared.get("creationflags") or 0) | create_no_window
            )

        startupinfo = prepared.get("startupinfo")
        if startupinfo is None:
            startupinfo_factory = getattr(self._module, "STARTUPINFO", None)
            if startupinfo_factory is not None:
                startupinfo = startupinfo_factory()
        if startupinfo is not None:
            startupinfo.dwFlags |= int(
                getattr(self._module, "STARTF_USESHOWWINDOW", 0)
            )
            startupinfo.wShowWindow = int(getattr(self._module, "SW_HIDE", 0))
            prepared["startupinfo"] = startupinfo

        return prepared

    @staticmethod
    def _relay_output(stream: TextIO | None, value: str | bytes | None) -> None:
        if stream is None or not value:
            return
        if isinstance(value, bytes):
            binary_stream = getattr(stream, "buffer", None)
            if binary_stream is not None:
                binary_stream.write(value)
                binary_stream.flush()
                return
            value = value.decode(errors="replace")
        stream.write(value)
        stream.flush()

    def run(self, *args: Any, **kwargs: Any) -> Any:
        command = args[0] if args else kwargs.get("args")
        prepared = self._prepare_kwargs(command, kwargs)
        relay_output = (
            self._platform == "win32"
            and not kwargs.get("show_window")
            and not prepared.get("capture_output")
            and "stdout" not in prepared
            and "stderr" not in prepared
        )
        if relay_output:
            prepared["stdout"] = self._module.PIPE
            prepared["stderr"] = self._module.PIPE
        try:
            result = self._module.run(*args, **prepared)
        except self._module.CalledProcessError as exc:
            if relay_output:
                self._relay_output(sys.stdout, exc.stdout)
                self._relay_output(sys.stderr, exc.stderr)
            raise
        if relay_output:
            self._relay_output(sys.stdout, result.stdout)
            self._relay_output(sys.stderr, result.stderr)
        return result

    def Popen(self, *args: Any, **kwargs: Any) -> Any:  # noqa: N802
        command = args[0] if args else kwargs.get("args")
        return self._module.Popen(
            *args,
            **self._prepare_kwargs(command, kwargs),
        )


def hidden_subprocess(module: ModuleType) -> HiddenSubprocess:
    """Wrap a subprocess module with Windows background-launch behavior."""
    return HiddenSubprocess(module)
