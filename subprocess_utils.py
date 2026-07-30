"""Subprocess helpers that keep project process trees hidden on Windows."""
from __future__ import annotations

import os
import sys
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


class HiddenSubprocess:
    """Proxy ``subprocess`` while preventing child console-window activation."""

    def __init__(
        self,
        module: ModuleType,
        *,
        platform: str | None = None,
    ) -> None:
        self._module = module
        self._platform = platform or sys.platform

    def __getattr__(self, name: str) -> Any:
        return getattr(self._module, name)

    def _prepare_kwargs(self, command: Any, kwargs: dict[str, Any]) -> dict[str, Any]:
        prepared = dict(kwargs)
        show_window = bool(prepared.pop("show_window", False))
        if self._platform != "win32" or show_window:
            return prepared

        creationflags = int(prepared.get("creationflags") or 0)
        create_no_window = int(getattr(self._module, "CREATE_NO_WINDOW", 0))
        create_new_console = int(getattr(self._module, "CREATE_NEW_CONSOLE", 0))
        if create_new_console:
            # A process created with CREATE_NO_WINDOW has no console to pass to
            # Git/gh helpers, so their descendants can create visible consoles.
            # Give the direct child one hidden console instead; its whole process
            # tree then inherits that same hidden console.
            creationflags &= ~create_no_window
            creationflags |= create_new_console
            prepared["creationflags"] = creationflags
        elif create_no_window:
            prepared["creationflags"] = creationflags | create_no_window

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

        if _is_github_cli(command):
            env = dict(prepared.get("env") or os.environ)
            env.setdefault("GH_TELEMETRY", "false")
            env.setdefault("TZ", "Asia/Shanghai")
            prepared["env"] = env
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
