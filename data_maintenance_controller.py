"""Data backup, restore, audit, and cleanup orchestration without Tk."""
from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from job_identity import normalize_job_name


ACTIVITY_KEYS = {
    "backup": "last_data_backup_at",
    "restore": "last_data_restore_at",
    "diagnostic_export": "last_diagnostic_export_at",
}


@dataclass(frozen=True)
class CandidateClearResult:
    """Atomic candidate cleanup outcome plus resume-file reclamation."""

    removed_count: int
    greeted_kept_count: int
    blacklist_kept_count: int
    resume_cleanup: Any

    def message(self, scope: str, selected_job: str) -> str:
        if scope == "current":
            text = f"已清空岗位「{selected_job}」的 {self.removed_count} 条候选人数据"
        else:
            text = f"已清空全部 {self.removed_count} 条候选人数据"
        if self.greeted_kept_count:
            text += f"，保留 {self.greeted_kept_count} 条已打招呼记录"
        if self.blacklist_kept_count:
            text += f"，保留 {self.blacklist_kept_count} 条黑名单记录"
        return text


@dataclass(frozen=True)
class ResumeRepairResult:
    """Repair transaction and its mandatory post-repair audit."""

    repair: Any
    cleanup: Any
    remaining: Any


class DataMaintenanceController:
    """Coordinate local data operations through explicitly supplied services."""

    @staticmethod
    def operation_busy(
        *,
        scan_running: bool,
        contact_running: bool,
        contact_preparing: bool,
        maintenance_running: bool,
    ) -> bool:
        return any(
            (scan_running, contact_running, contact_preparing, maintenance_running)
        )

    @staticmethod
    def format_backup_summary(result: Mapping[str, Any]) -> str:
        return (
            f"岗位 {int(result.get('job_count') or 0)} 个，"
            f"候选人 {int(result.get('candidate_count') or 0)} 人，"
            f"联系清单 {int(result.get('queue_count') or 0)} 项，"
            f"简历副本 {int(result.get('resume_count') or 0)} 份"
        )

    @staticmethod
    def backup_summary_metrics(
        result: Mapping[str, Any],
    ) -> tuple[tuple[str, str], ...]:
        return (
            ("岗位", f"{int(result.get('job_count') or 0)} 个"),
            ("候选人", f"{int(result.get('candidate_count') or 0)} 人"),
            ("联系清单", f"{int(result.get('queue_count') or 0)} 项"),
            ("简历副本", f"{int(result.get('resume_count') or 0)} 份"),
        )

    @staticmethod
    def format_time(value: object) -> str:
        text = str(value or "").strip()
        if not text:
            return "暂无记录"
        try:
            timestamp = datetime.fromisoformat(text)
        except (TypeError, ValueError):
            return "暂无记录"
        if timestamp.tzinfo is not None:
            timestamp = timestamp.astimezone()
        return timestamp.strftime("%Y-%m-%d %H:%M")

    @classmethod
    def remember_success(
        cls,
        preferences: Mapping[str, Any],
        activity: str,
        *,
        when: datetime | None = None,
    ) -> tuple[dict[str, Any], str]:
        key = ACTIVITY_KEYS.get(activity)
        if key is None:
            raise ValueError(f"未知的数据维护操作：{activity}")
        timestamp = when or datetime.now().astimezone()
        if timestamp.tzinfo is None:
            timestamp = timestamp.astimezone()
        value = timestamp.isoformat(timespec="seconds")
        updated = dict(preferences)
        updated[key] = value
        return updated, value

    @classmethod
    def backup_note(
        cls,
        preferences: Mapping[str, Any],
        *,
        backup_at: object | None = None,
        restore_at: object | None = None,
        backup_summary: str = "",
        restore_summary: str = "",
    ) -> str:
        backup_value = (
            preferences.get(ACTIVITY_KEYS["backup"])
            if backup_at is None
            else backup_at
        )
        restore_value = (
            preferences.get(ACTIVITY_KEYS["restore"])
            if restore_at is None
            else restore_at
        )
        backup_line = f"最近备份：{cls.format_time(backup_value)}"
        restore_line = f"最近恢复：{cls.format_time(restore_value)}"
        if backup_summary:
            backup_line += f" · {backup_summary}"
        if restore_summary:
            restore_line += f" · {restore_summary}"
        return f"{backup_line}\n{restore_line}"

    @classmethod
    def diagnostic_note(
        cls,
        preferences: Mapping[str, Any],
        *,
        exported_at: object | None = None,
        summary: str = "",
    ) -> str:
        value = (
            preferences.get(ACTIVITY_KEYS["diagnostic_export"])
            if exported_at is None
            else exported_at
        )
        line = f"最近导出：{cls.format_time(value)}"
        return f"{line} · {summary}" if summary else line

    @staticmethod
    def count_greeted(
        candidates: Sequence[Mapping[str, Any]],
        selected_job: str,
    ) -> int:
        if selected_job == "全部岗位":
            return sum(1 for candidate in candidates if candidate.get("greet_sent"))
        job_name = normalize_job_name(selected_job)
        return sum(
            1
            for candidate in candidates
            if candidate.get("greet_sent")
            and normalize_job_name(candidate.get("job_name")) == job_name
        )

    @staticmethod
    def clear_candidates(
        *,
        scope: str,
        selected_job: str,
        keep_greeted: bool,
        candidates_path: Path,
        base_dir: Path,
        mutate_with_resume_cleanup: Callable[..., tuple[Any, Any]],
        clear_in_place: Callable[..., Any],
    ) -> CandidateClearResult:
        """Apply one candidate cleanup inside the storage transaction."""
        cleanup_outcome = None

        def clear_snapshot(candidates: list[dict[str, Any]]) -> int:
            nonlocal cleanup_outcome
            cleanup_outcome = clear_in_place(
                candidates,
                scope=scope,
                selected_job=selected_job,
                keep_greeted=keep_greeted,
            )
            return int(cleanup_outcome.removed_count)

        _result, resume_cleanup = mutate_with_resume_cleanup(
            clear_snapshot,
            candidates_path,
            base_dir=base_dir,
        )
        if cleanup_outcome is None:
            raise RuntimeError("候选人清理事务未返回结果")
        return CandidateClearResult(
            removed_count=int(cleanup_outcome.removed_count),
            greeted_kept_count=int(cleanup_outcome.greeted_kept_count),
            blacklist_kept_count=int(cleanup_outcome.blacklist_kept_count),
            resume_cleanup=resume_cleanup,
        )

    @staticmethod
    def audit_resumes(
        candidates_path: Path,
        base_dir: Path,
        *,
        reader: Callable[[Path], list[dict[str, Any]]],
        auditor: Callable[..., Any],
    ) -> Any:
        candidates = reader(candidates_path)
        return auditor(candidates, base_dir=base_dir)

    @classmethod
    def repair_resumes(
        cls,
        candidates_path: Path,
        base_dir: Path,
        *,
        repairer: Callable[..., tuple[Any, Any]],
        reader: Callable[[Path], list[dict[str, Any]]],
        auditor: Callable[..., Any],
    ) -> ResumeRepairResult:
        repair, cleanup = repairer(candidates_path, base_dir=base_dir)
        remaining = cls.audit_resumes(
            candidates_path,
            base_dir,
            reader=reader,
            auditor=auditor,
        )
        return ResumeRepairResult(repair, cleanup, remaining)

    @staticmethod
    def create_backup(
        base_dir: Path,
        destination: str,
        *,
        creator: Callable[[Path, str], Mapping[str, Any]],
    ) -> Mapping[str, Any]:
        return creator(base_dir, destination)

    @staticmethod
    def inspect_backup(
        source: str,
        *,
        inspector: Callable[[str], Mapping[str, Any]],
    ) -> Mapping[str, Any]:
        return inspector(source)

    @staticmethod
    def restore_backup(
        base_dir: Path,
        source: str,
        *,
        restorer: Callable[[Path, str], Mapping[str, Any]],
    ) -> Mapping[str, Any]:
        return restorer(base_dir, source)

    @staticmethod
    def create_diagnostic(
        base_dir: Path,
        destination: str,
        *,
        app_version: str,
        runtime_context: Mapping[str, Any],
        creator: Callable[..., Mapping[str, Any]],
    ) -> Mapping[str, Any]:
        return creator(
            base_dir,
            destination,
            app_version=app_version,
            runtime_context=dict(runtime_context),
        )
