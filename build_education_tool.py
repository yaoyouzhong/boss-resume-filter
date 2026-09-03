"""构建独立的学历证书核验助手 EXE。"""
from __future__ import annotations

import argparse
import subprocess
from pathlib import Path

from build import (
    _check_tkinter_packaging_support,
    _pyinstaller_tk_args,
    run_in_venv,
)
from subprocess_utils import hidden_subprocess

subprocess = hidden_subprocess(subprocess)

BASE_DIR = Path(__file__).resolve().parent
PACK_ENV_DIR = BASE_DIR / "pack_venv"
PACK_PYTHON = PACK_ENV_DIR / "Scripts" / "python.exe"
PACK_CONFIG = PACK_ENV_DIR / "pyvenv.cfg"


def _education_tk_args() -> tuple[list[str], dict[str, str]]:
    """Map the shared Conda Tcl/Tk files to PyInstaller runtime-hook paths."""
    arguments, environment = _pyinstaller_tk_args()
    mapped_arguments = [
        argument.replace(r";tcl\tcl8.6", ";_tcl_data").replace(
            r";tcl\tk8.6",
            ";_tk_data",
        )
        for argument in arguments
    ]
    environment["EDUCATION_TOOL_TK_FALLBACK"] = "1"
    return mapped_arguments, environment


def _check_pack_environment() -> None:
    """Require the repository's isolated packaging environment."""
    if not PACK_PYTHON.is_file() or not PACK_CONFIG.is_file():
        raise RuntimeError(f"独立打包环境不存在：{PACK_PYTHON}")

    config_text = PACK_CONFIG.read_text(encoding="utf-8", errors="replace")
    if "include-system-site-packages = false" not in config_text.lower():
        raise RuntimeError("pack_venv 未隔离系统依赖，已停止学历工具构建")


def _check_pack_dependencies(build_environment: dict[str, str]) -> None:
    """Import required modules inside the actual packaging environment."""
    modules = (
        "DrissionPage",
        "PIL",
        "PyInstaller",
        "education_certificate",
        "education_tool",
        "education_tool_security",
        "pdfminer",
        "tkinter",
        "win32ctypes.pywin32.win32cred",
    )
    script = (
        "import importlib; "
        f"[importlib.import_module(name) for name in {modules!r}]; "
        "print('pack environment imports passed')"
    )
    subprocess.run(
        [str(PACK_PYTHON), "-c", script],
        cwd=BASE_DIR,
        env=build_environment,
        check=True,
    )


def main() -> None:
    run_in_venv(__file__)
    parser = argparse.ArgumentParser(description="构建学历证书核验助手")
    parser.add_argument(
        "--check",
        action="store_true",
        help="只检查独立入口和构建依赖，不生成密钥或 EXE",
    )
    parser.add_argument(
        "--debug-console",
        action="store_true",
        help="生成带控制台的诊断副本，用于捕获打包层启动异常",
    )
    args = parser.parse_args()
    _check_pack_environment()
    _check_tkinter_packaging_support()
    tkinter_args, build_environment = _education_tk_args()
    _check_pack_dependencies(build_environment)
    if args.check:
        print("学历证书核验助手构建检查通过")
        return

    artifact_name = (
        "EducationCertificateToolDebug"
        if args.debug_console
        else "EducationCertificateTool"
    )
    window_mode = "--console" if args.debug_console else "--noconsole"
    command = [
        str(PACK_PYTHON),
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onefile",
        window_mode,
        "--runtime-tmpdir",
        r"%LOCALAPPDATA%",
        "--name",
        artifact_name,
        "--icon",
        str(BASE_DIR / "education_tool.ico"),
        "--additional-hooks-dir",
        str(BASE_DIR / "pyinstaller-hooks"),
        *tkinter_args,
        "--hidden-import",
        "education_certificate",
        "--collect-submodules",
        "pdfminer",
        "--hidden-import",
        "win32ctypes.pywin32.win32cred",
        "--hidden-import",
        "win32ctypes.pywin32.pywintypes",
        "--collect-submodules",
        "win32ctypes.core.ctypes",
        "--collect-submodules",
        "win32ctypes.core.cffi",
        "--hidden-import",
        "PIL.ImageTk",
        "--exclude-module",
        "docx",
        "--exclude-module",
        "striprtf",
        "--exclude-module",
        "tkcalendar",
        "--exclude-module",
        "keyring",
        "--exclude-module",
        "cv2",
        "--exclude-module",
        "numpy",
        "--exclude-module",
        "numpy.libs",
        "--exclude-module",
        "scipy",
        "--exclude-module",
        "matplotlib",
        "--exclude-module",
        "pandas",
        "--exclude-module",
        "pytest",
        "--exclude-module",
        "PIL._avif",
        str(BASE_DIR / "education_tool.py"),
    ]
    subprocess.run(
        command,
        cwd=BASE_DIR,
        env=build_environment,
        check=True,
    )


if __name__ == "__main__":
    main()
