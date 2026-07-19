import importlib.util
import os
from pathlib import Path
import tempfile
from unittest.mock import patch


BASE_DIR = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "boss_resume_filter_gitee_actions_probe",
    BASE_DIR / "scripts" / "gitee_actions_probe.py",
)
assert SPEC and SPEC.loader
probe = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(probe)


class _Response:
    status_code = 200


class _Session:
    def get(self, *_args, **_kwargs):
        return _Response()


def _artifacts(directory: Path) -> list[Path]:
    paths = [directory / name for name in probe.ARTIFACT_NAMES]
    for index, path in enumerate(paths, start=1):
        path.write_bytes(b"x" * index)
    return paths


def test_probe_uploads_serially_verifies_sizes_and_cleans_remote_state():
    with tempfile.TemporaryDirectory() as temp_dir:
        directory = Path(temp_dir)
        artifacts = _artifacts(directory)
        events: list[str] = []
        remote = {
            path.name: {"name": path.name, "size": path.stat().st_size}
            for path in artifacts
        }

        def fake_run(args, **_kwargs):
            events.append("local_tag_delete" if "-d" in args else "local_tag_create")
            return probe.subprocess.CompletedProcess(args, 0, "", "")

        with (
            patch.dict(os.environ, {"GITEE_TOKEN": "token"}),
            patch.object(probe, "STATE_PATH", directory / ".release_state.json"),
            patch.object(probe, "_session", return_value=_Session()),
            patch.object(
                probe,
                "_git_push_tag",
                side_effect=lambda _tag, _token, delete=False: events.append(
                    "remote_tag_delete" if delete else "remote_tag_push"
                ),
            ),
            patch.object(probe, "_create_release", side_effect=lambda *_: events.append("release_create") or 7),
            patch.object(
                probe,
                "_upload_one",
                side_effect=lambda _session, _release, _token, path: events.append(path.name) or {},
            ),
            patch.object(probe, "_fetch_assets", return_value=remote),
            patch.object(
                probe,
                "_delete_release",
                side_effect=lambda *_: events.append("release_delete"),
            ),
            patch.object(probe.subprocess, "run", side_effect=fake_run),
        ):
            probe.run_probe("gitee-actions-probe-test", directory)

        assert events == [
            "local_tag_create",
            "remote_tag_push",
            "release_create",
            *probe.ARTIFACT_NAMES,
            "release_delete",
            "remote_tag_delete",
            "local_tag_delete",
        ]
        state = (directory / ".release_state.json").read_text(encoding="utf-8")
        assert '"status": "complete"' in state
        assert "Temporary Release and tag removed" in state


def test_probe_cleans_tag_when_release_creation_fails():
    with tempfile.TemporaryDirectory() as temp_dir:
        directory = Path(temp_dir)
        _artifacts(directory)
        events: list[str] = []

        def fake_run(args, **_kwargs):
            events.append("local_tag_delete" if "-d" in args else "local_tag_create")
            return probe.subprocess.CompletedProcess(args, 0, "", "")

        with (
            patch.dict(os.environ, {"GITEE_TOKEN": "token"}),
            patch.object(probe, "STATE_PATH", directory / ".release_state.json"),
            patch.object(probe, "_session", return_value=_Session()),
            patch.object(
                probe,
                "_git_push_tag",
                side_effect=lambda _tag, _token, delete=False: events.append(
                    "remote_tag_delete" if delete else "remote_tag_push"
                ),
            ),
            patch.object(probe, "_create_release", side_effect=probe.ProbeError("create failed")),
            patch.object(probe.subprocess, "run", side_effect=fake_run),
        ):
            try:
                probe.run_probe("gitee-actions-probe-test", directory)
            except probe.ProbeError as exc:
                assert "create failed" in str(exc)
            else:
                raise AssertionError("probe should fail")

        assert events == [
            "local_tag_create",
            "remote_tag_push",
            "remote_tag_delete",
            "local_tag_delete",
        ]
