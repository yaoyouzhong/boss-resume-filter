"""Benchmark bounded Gitee Release uploads on an isolated temporary prerelease."""
from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parents[1]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

import build  # noqa: E402
import release_ci  # noqa: E402


AUTHORIZATION = "确认执行 Gitee 上传基准测试并清理临时对象"
GITEE_API = "https://gitee.com/api/v5/repos/yaoyouzhong/boss-resume-filter"
DEFAULT_ORDER = (1, 2, 3, 3, 2, 1)


class BenchmarkError(RuntimeError):
    """The isolated upload benchmark could not satisfy its safety contract."""


def _parse_order(value: str) -> tuple[int, ...]:
    try:
        order = tuple(int(item.strip()) for item in value.split(","))
    except ValueError as exc:
        raise argparse.ArgumentTypeError("并发顺序必须是逗号分隔的 1、2、3") from exc
    if not order or any(workers not in {1, 2, 3} for workers in order):
        raise argparse.ArgumentTypeError("并发顺序只允许 1、2、3")
    return order


def _require_safe_args(args: argparse.Namespace) -> None:
    if not args.execute:
        raise BenchmarkError("必须显式传入 --execute")
    if args.authorization != AUTHORIZATION:
        raise BenchmarkError("Gitee 上传基准测试授权文本不匹配")
    if not args.tag.startswith("transfer-benchmark-"):
        raise BenchmarkError("临时 tag 必须以 transfer-benchmark- 开头")
    if args.cooldown < 0 or args.cooldown > 60:
        raise BenchmarkError("轮间冷却必须在 0 到 60 秒之间")


class GiteeUploadBenchmark:
    """Create, measure, and always clean one isolated Gitee prerelease."""

    def __init__(
        self,
        *,
        version: str,
        tag: str,
        artifact_dir: Path,
        order: tuple[int, ...],
        cooldown: float,
        token: str,
    ) -> None:
        self.version = version
        self.tag = tag
        self.title = f"[TEMP] v{version} Gitee upload concurrency benchmark"
        self.body = (
            "Temporary prerelease for a controlled upload benchmark. "
            "It is deleted automatically after the test."
        )
        self.artifacts = [artifact_dir / name for name in release_ci.RELEASE_ARTIFACTS]
        self.expected = {path.name: path.stat().st_size for path in self.artifacts}
        self.order = order
        self.cooldown = cooldown
        self.token = token
        self.session = build._gitee_session(retries=0)
        self.release_id: int | None = None
        self.cleanup_allowed = False
        self.results: list[dict] = []

    def _api_releases(self) -> list[dict]:
        response = self.session.get(
            f"{GITEE_API}/releases",
            params={"access_token": self.token, "page": 1, "per_page": 100},
            timeout=30,
        )
        response.raise_for_status()
        return response.json()

    def _find_release(self) -> dict | None:
        return next(
            (item for item in self._api_releases() if item.get("tag_name") == self.tag),
            None,
        )

    def _fetch_assets(self, release_id: int) -> dict:
        return build._gitee_fetch_assets(GITEE_API, self.token, release_id)

    def _delete_expected_assets(self, release_id: int) -> None:
        assets = self._fetch_assets(release_id)
        unexpected = sorted(set(assets) - set(self.expected))
        if unexpected:
            raise BenchmarkError(f"临时 Release 出现非测试附件：{unexpected}")
        for name, asset in assets.items():
            build._gitee_delete_asset(
                GITEE_API,
                self.token,
                release_id,
                asset["id"],
                f"{self.tag}/{name}",
            )
        remaining = self._fetch_assets(release_id)
        if remaining:
            raise BenchmarkError(f"临时附件清理后仍残留：{sorted(remaining)}")

    def _create_prerelease(self) -> None:
        if self._find_release() or build._remote_tag_commit("gitee", self.tag):
            raise BenchmarkError("同名临时 Release 或 tag 已存在，拒绝接管")
        self.cleanup_allowed = True
        response = self.session.post(
            f"{GITEE_API}/releases",
            params={"access_token": self.token},
            json={
                "tag_name": self.tag,
                "name": self.title,
                "body": self.body,
                "target_commitish": "master",
                "prerelease": True,
            },
            timeout=30,
        )
        response.raise_for_status()
        self.release_id = int(response.json()["id"])
        print(
            f"CREATED prerelease id={self.release_id} tag={self.tag}",
            flush=True,
        )

    def _upload_round(self, index: int, workers: int) -> dict:
        if self.release_id is None:
            raise BenchmarkError("临时 Release 尚未创建")
        self._delete_expected_assets(self.release_id)
        per_file: dict[str, float] = {}
        timing_lock = threading.Lock()
        original_upload = build._gitee_upload_single

        def timed_upload(path: Path, *args, **kwargs):
            started = time.perf_counter()
            try:
                return original_upload(path, *args, **kwargs)
            finally:
                with timing_lock:
                    per_file[path.name] = round(time.perf_counter() - started, 3)

        cache = {
            "token": self.token,
            "owner": "yaoyouzhong",
            "repo": "boss-resume-filter",
            "tag": self.tag,
            "api_base": GITEE_API,
            "release_id": self.release_id,
            "existing": {},
        }
        print(
            f"ROUND {index}/{len(self.order)} START workers={workers}",
            flush=True,
        )
        build._gitee_upload_single = timed_upload
        started = time.perf_counter()
        cpu_started = time.process_time()
        try:
            downloads = build._gitee_upload_artifacts(
                "benchmark",
                self.title,
                self.body,
                self.artifacts,
                release_cache=cache,
                large_workers=workers,
                fail_fast=True,
            )
        finally:
            build._gitee_upload_single = original_upload
        elapsed = time.perf_counter() - started
        cpu_elapsed = time.process_time() - cpu_started
        if not downloads:
            raise BenchmarkError(f"第 {index} 轮没有返回完整上传结果")

        remote = self._fetch_assets(self.release_id)
        actual = {
            name: int(item.get("size") or 0)
            for name, item in remote.items()
        }
        if actual != self.expected:
            raise BenchmarkError(f"第 {index} 轮远端尺寸校验失败：{actual}")
        result = {
            "round": index,
            "workers": workers,
            "total_seconds": round(elapsed, 3),
            "cpu_seconds": round(cpu_elapsed, 3),
            "per_file_seconds": dict(sorted(per_file.items())),
            "total_bytes": sum(self.expected.values()),
            "throughput_mib_s": round(
                sum(self.expected.values()) / elapsed / 1024 / 1024,
                3,
            ),
        }
        print(
            "ROUND_RESULT " + json.dumps(result, ensure_ascii=False, sort_keys=True),
            flush=True,
        )
        return result

    def _delete_tag(self) -> None:
        if not build._remote_tag_commit("gitee", self.tag):
            return
        result = release_ci._run(
            ["git", "push", "gitee", f":refs/tags/{self.tag}"],
            capture_output=True,
        )
        if result.returncode != 0 or build._remote_tag_commit("gitee", self.tag):
            raise BenchmarkError("临时 Gitee tag 删除失败")
        print(f"CLEANUP tag deleted: {self.tag}", flush=True)

    def cleanup(self) -> None:
        """Delete only the temporary assets, prerelease, and tag created here."""
        if not self.cleanup_allowed:
            return
        release = self._find_release()
        if release:
            release_id = int(release["id"])
            self._delete_expected_assets(release_id)
            response = self.session.delete(
                f"{GITEE_API}/releases/{release_id}",
                params={"access_token": self.token},
                timeout=30,
            )
            response.raise_for_status()
            print(f"CLEANUP release deleted: {self.tag}", flush=True)
        self._delete_tag()
        if self._find_release() or build._remote_tag_commit("gitee", self.tag):
            raise BenchmarkError("临时 Gitee 对象清理后仍然存在")
        print("CLEANUP_OK", flush=True)

    def run(self) -> dict:
        """Execute the crossover benchmark and return round and grouped totals."""
        self._create_prerelease()
        for index, workers in enumerate(self.order, start=1):
            result = self._upload_round(index, workers)
            self.results.append(result)
            if self.release_id is None:
                raise BenchmarkError("临时 Release 状态丢失")
            self._delete_expected_assets(self.release_id)
            if index < len(self.order) and self.cooldown:
                print(f"COOLDOWN {self.cooldown:g}s", flush=True)
                time.sleep(self.cooldown)

        summary = {}
        for workers in sorted(set(self.order)):
            rounds = [item for item in self.results if item["workers"] == workers]
            totals = [item["total_seconds"] for item in rounds]
            throughputs = [item["throughput_mib_s"] for item in rounds]
            summary[str(workers)] = {
                "rounds": len(rounds),
                "mean_seconds": round(sum(totals) / len(totals), 3),
                "min_seconds": min(totals),
                "max_seconds": max(totals),
                "mean_throughput_mib_s": round(
                    sum(throughputs) / len(throughputs),
                    3,
                ),
            }
        return {"rounds": self.results, "summary": summary}


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Gitee Release 上传并发基准测试")
    parser.add_argument("--version", required=True)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--artifact-dir", type=Path, required=True)
    parser.add_argument("--order", type=_parse_order, default=DEFAULT_ORDER)
    parser.add_argument("--cooldown", type=float, default=10.0)
    parser.add_argument("--authorization", required=True)
    parser.add_argument("--execute", action="store_true")
    return parser


def main() -> int:
    args = _build_parser().parse_args()
    try:
        _require_safe_args(args)
        missing = [
            name
            for name in release_ci.RELEASE_ARTIFACTS
            if not (args.artifact_dir / name).is_file()
        ]
        if missing:
            raise BenchmarkError(f"基准产物缺失：{', '.join(missing)}")
        token = os.environ.get("GITEE_TOKEN", "")
        if not token:
            raise BenchmarkError("缺少 GITEE_TOKEN")
        benchmark = GiteeUploadBenchmark(
            version=args.version,
            tag=args.tag,
            artifact_dir=args.artifact_dir,
            order=args.order,
            cooldown=args.cooldown,
            token=token,
        )
        try:
            result = benchmark.run()
            print(
                "BENCHMARK_RESULTS "
                + json.dumps(result, ensure_ascii=False, sort_keys=True),
                flush=True,
            )
        finally:
            benchmark.cleanup()
        return 0
    except (BenchmarkError, build.requests.exceptions.RequestException) as exc:
        print(f"BENCHMARK_FAILED {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
