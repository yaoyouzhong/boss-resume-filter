"""
Run the stable unit regression suite.

This entry point intentionally avoids browser automation, real BOSS pages,
network calls, and the user's live job_config.json.
"""
from collections.abc import Callable
from contextlib import redirect_stderr, redirect_stdout
import importlib.util
from io import StringIO
from pathlib import Path
import sys
import time
import traceback


ROOT = Path(__file__).resolve().parents[1]
UNIT_DIR = Path(__file__).resolve().parent / "unit"

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _configure_output_encoding() -> None:
    """Keep Chinese test diagnostics readable in Windows terminals."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


def _load_module(path: Path):
    spec = importlib.util.spec_from_file_location(path.stem, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run_test(
    fn: Callable[[], None],
) -> tuple[Exception | None, str, str, str]:
    """运行单项测试；成功时静默，失败时保留完整输出和 traceback。"""
    captured_stdout = StringIO()
    captured_stderr = StringIO()
    try:
        # Process-wide BOSS cooldown is production state; each unit test must start clean.
        bossmaster = sys.modules.get("bossmaster")
        if bossmaster and hasattr(bossmaster, "clear_boss_access_block"):
            bossmaster.clear_boss_access_block()
        with redirect_stdout(captured_stdout), redirect_stderr(captured_stderr):
            fn()
        return None, captured_stdout.getvalue(), captured_stderr.getvalue(), ""
    except Exception as exc:
        return (
            exc,
            captured_stdout.getvalue(),
            captured_stderr.getvalue(),
            traceback.format_exc(),
        )


def _print_failure_output(captured_stdout: str, captured_stderr: str, trace: str) -> None:
    """失败时回放被捕获的过程输出，避免压缩日志损失诊断信息。"""
    if captured_stdout:
        print("--- captured stdout ---")
        print(captured_stdout.rstrip())
    if captured_stderr:
        print("--- captured stderr ---", file=sys.stderr)
        print(captured_stderr.rstrip(), file=sys.stderr)
    if trace:
        print(trace.rstrip(), file=sys.stderr)


def main() -> int:
    _configure_output_encoding()
    suite_started = time.perf_counter()
    verbose = "--verbose" in sys.argv[1:]
    test_files = sorted(UNIT_DIR.glob("test_*.py"))
    if not test_files:
        print("FAIL no unit test files found")
        return 1

    total = 0
    failures = 0

    for path in test_files:
        print(f"RUN  {path.name}...", flush=True)
        module_started = time.perf_counter()
        module_total = 0
        module_failures = 0
        module = _load_module(path)
        for name in sorted(dir(module)):
            if not name.startswith("test_"):
                continue
            fn = getattr(module, name)
            if not callable(fn):
                continue
            total += 1
            module_total += 1
            exc, captured_stdout, captured_stderr, trace = _run_test(fn)
            if exc is None:
                if verbose:
                    print(f"PASS {path.name}::{name}")
            elif isinstance(exc, AssertionError):
                failures += 1
                module_failures += 1
                print(f"FAIL {path.name}::{name}: {exc}")
                _print_failure_output(captured_stdout, captured_stderr, trace)
            else:
                failures += 1
                module_failures += 1
                print(f"ERROR {path.name}::{name}: {type(exc).__name__}: {exc}")
                _print_failure_output(captured_stdout, captured_stderr, trace)

        module_elapsed = time.perf_counter() - module_started
        if module_failures:
            print(
                f"FAIL {path.name}: {module_total} tests, "
                f"{module_failures} failures ({module_elapsed:.2f}s)"
            )
        else:
            print(f"PASS {path.name}: {module_total} tests ({module_elapsed:.2f}s)")

    suite_elapsed = time.perf_counter() - suite_started
    print(f"SUMMARY total={total} failures={failures} elapsed={suite_elapsed:.2f}s")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
