"""Compute the tracked product-input fingerprint used for test reuse."""
from __future__ import annotations

import argparse
import hashlib
import subprocess
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parents[1]
EXCLUDED_PATHS = frozenset({
    "AGENTS.md",
    "CHANGELOG.md",
    "CLAUDE.md",
    "README.md",
})


def product_code_fingerprint() -> str:
    """Hash tracked content except public release prose."""
    result = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=BASE_DIR,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="strict",
    )
    digest = hashlib.sha256()
    for relative in sorted(item for item in result.stdout.split("\0") if item):
        normalized = relative.replace("\\", "/")
        if normalized in EXCLUDED_PATHS:
            continue
        path = BASE_DIR / relative
        if not path.is_file():
            continue
        digest.update(normalized.encode("utf-8"))
        digest.update(b"\0")
        digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description="输出产品代码指纹")
    parser.add_argument("--github-output")
    args = parser.parse_args()
    fingerprint = product_code_fingerprint()
    print(fingerprint)
    if args.github_output:
        with open(args.github_output, "a", encoding="utf-8") as output:
            output.write(f"product_fingerprint={fingerprint}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
