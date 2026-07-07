"""Preview or remove local generated workspace files.

Default mode is a dry run. The script deliberately skips credentials,
candidate data, resumes, and local API config.
"""
from __future__ import annotations

import argparse
import shutil
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

SAFE_FILE_PATTERNS = (
    ".build_progress.json",
    ".build_state.json",
    ".last_update_check",
    "release_notes_cache.json",
    "test_output.txt",
    "build_log*.txt",
    "*.log",
    "*.spec",
)
SAFE_DIR_PATTERNS = (
    "__pycache__",
    ".pytest_cache",
    "tmp",
    "tmp_*",
)
PACKAGE_TARGETS = (
    "build",
    "dist",
    "Dummy.app",
)
BROWSER_TARGETS = (
    ".chrome_debug_port",
    ".chrome_profile",
    ".storage",
)
NEVER_DELETE = {
    ".env",
    "api_config.json",
    "api_config.local.json",
    "candidates_all.json",
    "candidates_all.json.bak",
    "candidates_all.xlsx",
    "job_config.json",
    "job_config.json.bak",
    "resumes",
}
SKIP_DIR_PARTS = {
    ".git",
    ".venv",
    ".venv-ci",
    "env",
    "ENV",
    "node_modules",
    "pack_venv",
    "venv",
}


def _under_root(path: Path) -> bool:
    try:
        path.resolve().relative_to(ROOT.resolve())
        return True
    except ValueError:
        return False


def _add_target(targets: set[Path], path: Path) -> None:
    if not path.exists():
        return
    if path.name in NEVER_DELETE:
        return
    if not _under_root(path):
        raise RuntimeError(f"refusing target outside workspace: {path}")
    targets.add(path)


def _is_under_skipped_dir(path: Path) -> bool:
    rel_parts = path.relative_to(ROOT).parts
    return any(part in SKIP_DIR_PARTS for part in rel_parts[:-1])


def collect_targets(include_packages: bool, include_browser_state: bool) -> list[Path]:
    targets: set[Path] = set()
    for pattern in SAFE_FILE_PATTERNS:
        for path in ROOT.glob(pattern):
            _add_target(targets, path)
    for pattern in SAFE_DIR_PATTERNS:
        for path in ROOT.rglob(pattern):
            if _is_under_skipped_dir(path):
                continue
            _add_target(targets, path)
    if include_packages:
        for name in PACKAGE_TARGETS:
            _add_target(targets, ROOT / name)
    if include_browser_state:
        for name in BROWSER_TARGETS:
            _add_target(targets, ROOT / name)
    return sorted(targets, key=lambda p: str(p.relative_to(ROOT)).lower())


def remove_target(path: Path) -> None:
    if path.is_dir():
        shutil.rmtree(path)
    else:
        path.unlink()


def main() -> int:
    parser = argparse.ArgumentParser(description="Preview or clean generated workspace files.")
    parser.add_argument("--apply", action="store_true", help="actually remove listed targets")
    parser.add_argument("--include-packages", action="store_true", help="include build/dist/package outputs")
    parser.add_argument("--include-browser-state", action="store_true", help="include Chrome/Drission runtime state")
    args = parser.parse_args()

    targets = collect_targets(args.include_packages, args.include_browser_state)
    if not targets:
        print("No cleanup targets found.")
        return 0

    action = "Removing" if args.apply else "Preview"
    print(f"{action} {len(targets)} cleanup target(s):")
    for path in targets:
        print(f"  {path.relative_to(ROOT)}")

    if not args.apply:
        print("\nDry run only. Add --apply to remove these targets.")
        return 0

    for path in targets:
        remove_target(path)
    print("Cleanup complete.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
