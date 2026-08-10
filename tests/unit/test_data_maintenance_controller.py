from datetime import datetime, timezone
import tempfile
from pathlib import Path
from types import SimpleNamespace

from data_maintenance_controller import DataMaintenanceController


def test_backup_summary_and_notes_are_privacy_safe_and_stable():
    result = {"job_count": 2, "candidate_count": 3, "queue_count": 4, "resume_count": 5}
    preferences, timestamp = DataMaintenanceController.remember_success(
        {},
        "backup",
        when=datetime(2026, 8, 10, 12, 30, tzinfo=timezone.utc),
    )

    assert DataMaintenanceController.format_backup_summary(result) == "岗位 2 个，候选人 3 人，联系清单 4 项，简历副本 5 份"
    assert DataMaintenanceController.backup_summary_metrics(result)[1] == ("候选人", "3 人")
    assert timestamp in preferences.values()
    assert "最近备份：" in DataMaintenanceController.backup_note(preferences)


def test_operation_busy_covers_each_writer_state():
    controller = DataMaintenanceController()
    assert not controller.operation_busy(
        scan_running=False,
        contact_running=False,
        contact_preparing=False,
        maintenance_running=False,
    )
    assert controller.operation_busy(
        scan_running=False,
        contact_running=False,
        contact_preparing=True,
        maintenance_running=False,
    )


def test_count_greeted_respects_normalized_job_scope():
    candidates = [
        {"job_name": "Java", "greet_sent": True},
        {"job_name": "Python", "greet_sent": True},
        {"job_name": "Java", "greet_sent": False},
    ]

    assert DataMaintenanceController.count_greeted(candidates, "全部岗位") == 2
    assert DataMaintenanceController.count_greeted(candidates, "Java") == 1


def test_clear_candidates_runs_rule_inside_injected_atomic_transaction():
    records = [
        {"job_name": "Java", "greet_sent": False},
        {"job_name": "Java", "greet_sent": True},
    ]
    resume_cleanup = SimpleNamespace(deleted_file_count=1)

    def mutate(callback, _path, **_kwargs):
        return callback(records), resume_cleanup

    def clear_in_place(candidates, **_kwargs):
        candidates[:] = candidates[1:]
        return SimpleNamespace(
            removed_count=1,
            greeted_kept_count=1,
            blacklist_kept_count=0,
        )

    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        result = DataMaintenanceController.clear_candidates(
            scope="current",
            selected_job="Java",
            keep_greeted=True,
            candidates_path=root / "candidates.json",
            base_dir=root,
            mutate_with_resume_cleanup=mutate,
            clear_in_place=clear_in_place,
        )

    assert result.removed_count == 1
    assert records == [{"job_name": "Java", "greet_sent": True}]
    assert result.resume_cleanup is resume_cleanup


def test_resume_repair_always_returns_post_repair_audit():
    events = []
    report = SimpleNamespace(issue_count=0)

    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        result = DataMaintenanceController.repair_resumes(
            root / "candidates.json",
            root,
            repairer=lambda *_args, **_kwargs: (
                events.append("repair") or "repair-result",
                "cleanup-result",
            ),
            reader=lambda _path: events.append("read") or [],
            auditor=lambda _candidates, **_kwargs: events.append("audit") or report,
        )

    assert events == ["repair", "read", "audit"]
    assert result.remaining is report
