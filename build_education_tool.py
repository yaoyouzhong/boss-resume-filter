"""构建独立的学历证书核验助手 EXE。"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
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


def _resolve_build_python(ci: bool) -> Path:
    """Use hosted Python only in the guarded Windows release workflow."""
    if ci:
        if (
            os.environ.get("GITHUB_ACTIONS") != "true"
            or os.environ.get("RUNNER_OS") != "Windows"
            or os.name != "nt"
        ):
            raise RuntimeError("--ci 只能用于 GitHub Actions 的 Windows 发布任务")
        return Path(sys.executable)

    run_in_venv(__file__)
    _check_pack_environment()
    return PACK_PYTHON


def _check_pack_dependencies(
    build_environment: dict[str, str],
    build_python: Path,
) -> None:
    """Import required modules inside the actual packaging environment."""
    modules = (
        "DrissionPage",
        "PIL",
        "PyInstaller",
        "education_certificate",
        "education_tool",
        "education_tool_security",
        "pypdf",
        "tkinter",
        "win32ctypes.pywin32.win32cred",
    )
    script = (
        "import importlib; "
        f"[importlib.import_module(name) for name in {modules!r}]; "
        "print('pack environment imports passed')"
    )
    subprocess.run(
        [str(build_python), "-c", script],
        cwd=BASE_DIR,
        env=build_environment,
        check=True,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="构建学历证书核验助手")
    parser.add_argument(
        "--check",
        action="store_true",
        help="只检查独立入口和构建依赖，不生成密钥或 EXE",
    )
    parser.add_argument(
        "--ci",
        action="store_true",
        help="仅供 GitHub Actions Windows 正式发布任务使用",
    )
    parser.add_argument(
        "--debug-console",
        action="store_true",
        help="生成带控制台的诊断副本，用于捕获打包层启动异常",
    )
    args = parser.parse_args()
    build_python = _resolve_build_python(args.ci)
    _check_tkinter_packaging_support()
    tkinter_args, build_environment = _education_tk_args()
    _check_pack_dependencies(build_environment, build_python)
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
        str(build_python),
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
        "--hidden-import",
        "win32ctypes.pywin32.win32cred",
        "--hidden-import",
        "win32ctypes.pywin32.pywintypes",
        "--collect-submodules",
        "win32ctypes.core.ctypes",
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
        "--exclude-module",
        "pdfminer",
        "--exclude-module",
        "cryptography",
        "--exclude-module",
        "Crypto",
        "--exclude-module",
        "bossmaster",
        "--exclude-module",
        "openpyxl",
        "--exclude-module",
        "lxml.objectify",
        "--exclude-module",
        "lxml.builder",
        "--exclude-module",
        "lxml.html.diff",
        "--exclude-module",
        "lxml.html._difflib",
        "--exclude-module",
        "lxml.isoschematron",
        "--exclude-module",
        "lxml.sax",
        "--exclude-module",
        "cffi",
        "--exclude-module",
        "_cffi_backend",
        "--exclude-module",
        "pycparser",
        "--exclude-module",
        "setuptools",
        str(BASE_DIR / "education_tool.py"),
    ]
    subprocess.run(
        command,
        cwd=BASE_DIR,
        env=build_environment,
        check=True,
    )
    artifact_path = BASE_DIR / "dist" / f"{artifact_name}.exe"
    subprocess.run(
        [str(artifact_path), "--smoke-test"],
        cwd=BASE_DIR,
        env=build_environment,
        check=True,
        timeout=120,
    )
    subprocess.run(
        [str(artifact_path), "--credential-smoke-test"],
        cwd=BASE_DIR,
        env=build_environment,
        check=True,
        timeout=120,
    )
    print(f"学历证书核验助手构建和烟测通过：{artifact_path}")


if __name__ == "__main__":
    main()
