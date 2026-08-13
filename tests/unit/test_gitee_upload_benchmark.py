import argparse
import importlib.util
from pathlib import Path
import tempfile
from unittest.mock import patch


BASE_DIR = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "boss_resume_filter_gitee_upload_benchmark",
    BASE_DIR / "scripts" / "gitee_upload_benchmark.py",
)
assert SPEC and SPEC.loader
benchmark_module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(benchmark_module)


def _args(**overrides) -> argparse.Namespace:
    values = {
        "execute": True,
        "authorization": benchmark_module.AUTHORIZATION,
        "tag": "transfer-benchmark-20260813",
        "cooldown": 10,
    }
    values.update(overrides)
    return argparse.Namespace(**values)


def _raises(error_type, message: str):
    class ErrorContext:
        def __enter__(self):
            return None

        def __exit__(self, exc_type, exc, _traceback):
            assert exc_type is error_type
            assert message in str(exc)
            return True

    return ErrorContext()


def test_benchmark_requires_exact_authorization_and_temporary_tag_prefix():
    with _raises(benchmark_module.BenchmarkError, "授权文本不匹配"):
        benchmark_module._require_safe_args(_args(authorization="确认发布 v2.28.1"))

    with _raises(benchmark_module.BenchmarkError, "临时 tag"):
        benchmark_module._require_safe_args(_args(tag="v2.28.1"))


def test_benchmark_cleanup_does_not_take_over_preexisting_remote_objects():
    with tempfile.TemporaryDirectory() as temp_dir:
        artifact_dir = Path(temp_dir)
        for name in benchmark_module.release_ci.RELEASE_ARTIFACTS:
            (artifact_dir / name).write_bytes(b"artifact")
        with patch.object(
            benchmark_module.build,
            "_gitee_session",
            return_value=object(),
        ):
            benchmark = benchmark_module.GiteeUploadBenchmark(
                version="2.28.1",
                tag="transfer-benchmark-20260813",
                artifact_dir=artifact_dir,
                order=(1,),
                cooldown=0,
                token="token",
            )
        with (
            patch.object(benchmark, "_find_release") as find_release,
            patch.object(benchmark, "_delete_expected_assets") as delete_assets,
            patch.object(benchmark, "_delete_tag") as delete_tag,
        ):
            benchmark.cleanup()

    find_release.assert_not_called()
    delete_assets.assert_not_called()
    delete_tag.assert_not_called()


def test_benchmark_cleanup_removes_only_created_release_and_tag():
    class FakeResponse:
        def raise_for_status(self):
            return None

    class FakeSession:
        def __init__(self):
            self.deleted_urls = []

        def delete(self, url, **_kwargs):
            self.deleted_urls.append(url)
            return FakeResponse()

    with tempfile.TemporaryDirectory() as temp_dir:
        artifact_dir = Path(temp_dir)
        for name in benchmark_module.release_ci.RELEASE_ARTIFACTS:
            (artifact_dir / name).write_bytes(b"artifact")
        session = FakeSession()
        with patch.object(
            benchmark_module.build,
            "_gitee_session",
            return_value=session,
        ):
            benchmark = benchmark_module.GiteeUploadBenchmark(
                version="2.28.1",
                tag="transfer-benchmark-20260813",
                artifact_dir=artifact_dir,
                order=(1,),
                cooldown=0,
                token="token",
            )
        benchmark.cleanup_allowed = True
        with (
            patch.object(
                benchmark,
                "_find_release",
                side_effect=[{"id": 7}, None],
            ),
            patch.object(benchmark, "_delete_expected_assets") as delete_assets,
            patch.object(benchmark, "_delete_tag") as delete_tag,
            patch.object(
                benchmark_module.build,
                "_remote_tag_commit",
                return_value=None,
            ),
        ):
            benchmark.cleanup()

    delete_assets.assert_called_once_with(7)
    delete_tag.assert_called_once_with()
    assert session.deleted_urls == [
        benchmark_module.GITEE_API + "/releases/7"
    ]
