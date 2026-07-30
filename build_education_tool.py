"""构建独立的学历证书核验助手 EXE。"""
from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import os
import secrets
import subprocess
from pathlib import Path

from education_tool_security import _keystream, _wrap_key
from subprocess_utils import hidden_subprocess

subprocess = hidden_subprocess(subprocess)

BASE_DIR = Path(__file__).resolve().parent
BUILD_DIR = BASE_DIR / "build" / "education-tool"
SECRET_PATH = BUILD_DIR / "education_tool_secret.json"
PACK_PYTHON = BASE_DIR / "pack_venv" / "Scripts" / "python.exe"


def _write_encrypted_secret() -> None:
    api_key = os.environ.get("EDUCATION_TOOL_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError(
            "缺少 EDUCATION_TOOL_API_KEY 环境变量，拒绝生成不含密钥的正式 EXE"
        )

    plaintext = api_key.encode("utf-8")
    nonce = secrets.token_bytes(16)
    data_key = secrets.token_bytes(32)
    ciphertext = bytes(
        a ^ b for a, b in zip(plaintext, _keystream(data_key, nonce, len(plaintext)))
    )
    wrapped_key = bytes(
        a ^ b for a, b in zip(data_key, _keystream(_wrap_key(), nonce, len(data_key)))
    )
    tag = hmac.new(data_key, nonce + ciphertext, hashlib.sha256).digest()

    BUILD_DIR.mkdir(parents=True, exist_ok=True)
    SECRET_PATH.write_text(
        json.dumps(
            {
                "nonce": base64.b64encode(nonce).decode("ascii"),
                "wrapped_key": base64.b64encode(wrapped_key).decode("ascii"),
                "ciphertext": base64.b64encode(ciphertext).decode("ascii"),
                "tag": base64.b64encode(tag).decode("ascii"),
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="构建学历证书核验助手")
    parser.add_argument(
        "--check",
        action="store_true",
        help="只检查独立入口和构建依赖，不生成密钥或 EXE",
    )
    args = parser.parse_args()
    if args.check:
        import DrissionPage
        import PIL
        import pdfminer
        import education_certificate
        import education_tool

        if not PACK_PYTHON.is_file():
            raise RuntimeError(f"独立打包环境不存在：{PACK_PYTHON}")
        print("学历证书核验助手构建检查通过")
        return

    if not PACK_PYTHON.is_file():
        raise RuntimeError(f"独立打包环境不存在：{PACK_PYTHON}")

    _write_encrypted_secret()
    separator = ";" if os.name == "nt" else ":"
    command = [
        str(PACK_PYTHON),
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onefile",
        "--noconsole",
        "--runtime-tmpdir",
        r"%LOCALAPPDATA%",
        "--name",
        "EducationCertificateTool",
        "--icon",
        str(BASE_DIR / "education_tool.ico"),
        "--additional-hooks-dir",
        str(BASE_DIR / "pyinstaller-hooks"),
        "--add-data",
        f"{SECRET_PATH}{separator}.",
        "--hidden-import",
        "education_certificate",
        "--collect-submodules",
        "pdfminer",
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
    try:
        subprocess.run(command, cwd=BASE_DIR, check=True)
    finally:
        SECRET_PATH.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
