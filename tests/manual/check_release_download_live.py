"""Read-only live GitHub artifact check, with isolated state and resume evidence."""
import argparse
import hashlib
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from scripts import release_ci


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--asset", action="append", default=[])
    parser.add_argument("--stop-after-mib", type=int, default=0)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    release_ci.RELEASE_STATE_PATH = args.output_dir / "state.json"
    release_ci.RELEASE_LOG_DIR = args.output_dir / "logs"
    assets = release_ci.build._get_github_release_assets("v" + args.version)
    names = args.asset or list(release_ci._release_artifacts(args.version))
    original_write = release_ci._write_release_state
    def checkpoint(*positional, **kwargs):
        original_write(*positional, **kwargs)
        if args.stop_after_mib and kwargs.get("downloaded_bytes", 0) >= args.stop_after_mib * 1024 * 1024:
            raise InterruptedError("Requested checkpoint interruption")
    release_ci._write_release_state = checkpoint
    results = []
    for name in names:
        if name not in release_ci._release_artifacts(args.version):
            raise ValueError("Unknown release asset")
        started = time.monotonic()
        try:
            path = release_ci._download_verified_github_artifact(
                "v" + args.version, name, assets, args.output_dir,
                release_sha="read-only-live-validation",
            )
        except InterruptedError:
            print("CHECKPOINT_INTERRUPTED", flush=True)
            return 75
        result = dict(name=name, size=path.stat().st_size,
                      sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                      elapsed_seconds=round(time.monotonic()-started, 3))
        results.append(result)
        (args.output_dir/"result.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
        print("LIVE_VERIFIED " + json.dumps(result), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
