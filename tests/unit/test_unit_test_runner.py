"""稳定回归入口的输出与失败诊断测试。"""

from contextlib import redirect_stderr, redirect_stdout
import importlib.util
from io import StringIO
from pathlib import Path
import tempfile


RUNNER_PATH = Path(__file__).resolve().parents[1] / "run_unit_tests.py"
RUNNER_SPEC = importlib.util.spec_from_file_location("stable_unit_runner", RUNNER_PATH)
runner = importlib.util.module_from_spec(RUNNER_SPEC)
RUNNER_SPEC.loader.exec_module(runner)


def _run_with_module(source: str) -> tuple[int, str, str]:
    with tempfile.TemporaryDirectory() as temp_dir:
        unit_dir = Path(temp_dir)
        (unit_dir / "test_sample.py").write_text(source, encoding="utf-8")
        original_unit_dir = runner.UNIT_DIR
        runner.UNIT_DIR = unit_dir
        stdout = StringIO()
        stderr = StringIO()
        try:
            with redirect_stdout(stdout), redirect_stderr(stderr):
                result = runner.main()
        finally:
            runner.UNIT_DIR = original_unit_dir
        return result, stdout.getvalue(), stderr.getvalue()


def test_runner_compacts_success_output_by_module():
    result, stdout, stderr = _run_with_module(
        "def test_success():\n"
        "    print('successful test noise')\n"
    )

    assert result == 0
    assert "RUN  test_sample.py..." in stdout
    assert "PASS test_sample.py: 1 tests" in stdout
    assert "SUMMARY total=1 failures=0" in stdout
    assert "successful test noise" not in stdout
    assert stderr == ""


def test_runner_replays_captured_output_for_failures():
    result, stdout, stderr = _run_with_module(
        "def test_failure():\n"
        "    print('failure diagnostic marker')\n"
        "    raise AssertionError('expected failure')\n"
    )

    assert result == 1
    assert "RUN  test_sample.py..." in stdout
    assert "FAIL test_sample.py::test_failure: expected failure" in stdout
    assert "failure diagnostic marker" in stdout
    assert "SUMMARY total=1 failures=1" in stdout
    assert "AssertionError: expected failure" in stderr
