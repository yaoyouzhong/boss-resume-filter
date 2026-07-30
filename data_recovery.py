"""Transactional migration, rotating recovery points, and portable backups."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import threading
import time
import uuid
import zipfile
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from contact_queue import (
    QUEUE_VERSION,
    load_contact_queue_snapshot,
    validate_contact_queue_snapshot,
)
from data_schema import (
    CANDIDATE_SCHEMA_VERSION,
    CONTACT_QUEUE_SCHEMA_VERSION,
    JOB_CONFIG_SCHEMA_VERSION,
    job_uuid_by_normalized_name,
    migrate_candidate_records,
    normalize_job_uuid,
    upgrade_job_config,
)
from job_config_store import (
    load_job_config_snapshot,
    validate_job_config,
)
from job_identity import normalize_job_name
from resume_store import resolve_managed_resume
from storage import load_candidates_all, validate_candidates_snapshot


BACKUP_FORMAT_VERSION = 1
DATA_MANIFEST_VERSION = 1
TRANSACTION_JOURNAL_VERSION = 1
AUTOMATIC_BACKUP_RETENTION = 7
_RUNTIME_FILES = (
    "job_config.json",
    "candidates_all.json",
    "contact_queue.json",
)
_PROCESS_LOCK = threading.RLock()


@dataclass(frozen=True)
class RuntimeDataPaths:
    root: Path
    job_config: Path
    candidates: Path
    contact_queue: Path
    data_manifest: Path
    journal: Path
    lock: Path
    transaction_dir: Path
    backups_dir: Path

    @classmethod
    def from_base_dir(cls, base_dir: str | Path) -> "RuntimeDataPaths":
        root = Path(base_dir).resolve()
        return cls(
            root=root,
            job_config=root / "job_config.json",
            candidates=root / "candidates_all.json",
            contact_queue=root / "contact_queue.json",
            data_manifest=root / ".data_manifest.json",
            journal=root / ".data_transaction.json",
            lock=root / ".data_transaction.lock",
            transaction_dir=root / ".data_transactions",
            backups_dir=root / "backups",
        )


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as file_obj:
        for chunk in iter(lambda: file_obj.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _json_bytes(payload: Any, *, indent: int = 2) -> bytes:
    return (
        json.dumps(payload, ensure_ascii=False, indent=indent) + "\n"
    ).encode("utf-8")


def _sync_parent(path: Path) -> None:
    if os.name == "nt":
        return
    directory_fd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def _atomic_write_bytes(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = Path(str(path) + ".tmp")
    try:
        with open(tmp_path, "wb") as file_obj:
            file_obj.write(content)
            file_obj.flush()
            os.fsync(file_obj.fileno())
        os.replace(tmp_path, path)
        _sync_parent(path)
    finally:
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                pass


def _atomic_write_json(path: Path, payload: Any) -> None:
    _atomic_write_bytes(path, _json_bytes(payload))


def _require_within(path: Path, parent: Path, label: str) -> Path:
    resolved = path.resolve()
    try:
        resolved.relative_to(parent.resolve())
    except ValueError as exc:
        raise ValueError(f"{label}路径越界") from exc
    if resolved == parent.resolve():
        raise ValueError(f"{label}不能指向根目录")
    return resolved


def _validate_runtime_relative_path(relative: str) -> Path:
    relative_path = Path(relative)
    if relative_path.is_absolute() or ".." in relative_path.parts:
        raise ValueError("事务目标路径无效")
    portable = relative_path.as_posix()
    if portable not in _RUNTIME_FILES and not portable.startswith("resumes/"):
        raise ValueError(f"事务不允许修改该文件：{relative}")
    return relative_path


def _lock_owner_alive(lock_path: Path) -> bool | None:
    """Return True/False for a readable PID, or None for malformed metadata."""
    try:
        raw_pid = lock_path.read_text(encoding="ascii").split(maxsplit=1)[0]
        pid = int(raw_pid)
    except (OSError, ValueError, IndexError):
        return None
    if pid == os.getpid():
        return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return None
    return True


@contextmanager
def _runtime_lock(paths: RuntimeDataPaths):
    with _PROCESS_LOCK:
        paths.lock.parent.mkdir(parents=True, exist_ok=True)
        deadline = time.monotonic() + 5
        lock_fd: int | None = None
        while lock_fd is None:
            try:
                lock_fd = os.open(
                    paths.lock,
                    os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                )
                os.write(
                    lock_fd,
                    f"{os.getpid()} {time.time():.6f}".encode("ascii"),
                )
            except FileExistsError:
                try:
                    age = time.time() - paths.lock.stat().st_mtime
                    owner_alive = _lock_owner_alive(paths.lock)
                    if (
                        (age > 60 and owner_alive is False)
                        or (age > 300 and owner_alive is None)
                    ):
                        paths.lock.unlink()
                        continue
                except FileNotFoundError:
                    continue
                if time.monotonic() >= deadline:
                    raise RuntimeError("等待数据事务锁超时")
                time.sleep(0.03)
        try:
            yield
        finally:
            os.close(lock_fd)
            try:
                paths.lock.unlink()
            except FileNotFoundError:
                pass


def _load_runtime_payloads(
    paths: RuntimeDataPaths,
) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
    config = load_job_config_snapshot(paths.job_config)
    candidates = load_candidates_all(str(paths.candidates))
    queue_payload = load_contact_queue_snapshot(paths.contact_queue)
    return config, candidates, queue_payload


def _current_job_names_by_uuid(config: dict[str, Any]) -> dict[str, str]:
    upgraded, _ = upgrade_job_config(config)
    result: dict[str, str] = {}
    for field in ("job_requirements", "jobs"):
        for display_name, rule in (upgraded.get(field) or {}).items():
            stable_id = normalize_job_uuid(rule.get("job_uuid"))
            if stable_id:
                result[stable_id] = display_name
    return result


def _upgrade_queue_payload(
    payload: dict[str, Any],
    config: dict[str, Any],
    candidates: list[dict[str, Any]],
) -> tuple[dict[str, Any], list[dict[str, str]]]:
    name_map = job_uuid_by_normalized_name(config)
    current_names = _current_job_names_by_uuid(config)
    candidate_map: dict[tuple[str, str], str] = {}
    for candidate in candidates:
        geek_id = str(candidate.get("geek_id") or "").strip()
        stable_id = normalize_job_uuid(candidate.get("job_uuid"))
        job_name = normalize_job_name(candidate.get("job_name")).casefold()
        if geek_id and stable_id and job_name:
            candidate_map[(geek_id, job_name)] = stable_id

    upgraded = {
        "version": CONTACT_QUEUE_SCHEMA_VERSION,
        "items": [],
    }
    unresolved: list[dict[str, str]] = []
    for raw_item in payload.get("items", []):
        item = dict(raw_item)
        geek_id = str(item.get("geek_id") or "").strip()
        job_name = str(item.get("job_name") or "")
        stable_id = normalize_job_uuid(item.get("job_uuid"))
        normalized_name = normalize_job_name(job_name).casefold()
        if not stable_id:
            stable_id = (
                candidate_map.get((geek_id, normalized_name), "")
                or name_map.get(normalized_name, "")
            )
        if stable_id:
            item["job_uuid"] = stable_id
            if stable_id in current_names:
                item["job_name"] = current_names[stable_id]
        else:
            item["job_uuid"] = ""
            unresolved.append({
                "geek_id": geek_id,
                "job_name": job_name,
            })
        upgraded["items"].append(item)
    validate_contact_queue_snapshot(upgraded)
    return upgraded, unresolved


def validate_runtime_consistency(
    config: dict[str, Any],
    candidates: list[dict[str, Any]],
    queue_payload: dict[str, Any],
) -> dict[str, Any]:
    """Return identity coverage without exposing candidate names."""
    upgraded_config, _ = upgrade_job_config(config)
    validate_job_config(upgraded_config)
    validate_candidates_snapshot(candidates)
    validate_contact_queue_snapshot(queue_payload)
    configured_ids = set(_current_job_names_by_uuid(upgraded_config))
    candidate_keys: set[tuple[str, str]] = set()
    unresolved_candidates = 0
    for candidate in candidates:
        geek_id = str(candidate.get("geek_id") or "").strip()
        stable_id = normalize_job_uuid(candidate.get("job_uuid"))
        if not stable_id or stable_id not in configured_ids:
            unresolved_candidates += 1
        if geek_id and stable_id:
            candidate_keys.add((geek_id, stable_id))

    unresolved_queue = 0
    orphan_queue = 0
    for item in queue_payload.get("items", []):
        geek_id = str(item.get("geek_id") or "").strip()
        stable_id = normalize_job_uuid(item.get("job_uuid"))
        if not stable_id or stable_id not in configured_ids:
            unresolved_queue += 1
        elif (geek_id, stable_id) not in candidate_keys:
            orphan_queue += 1
    return {
        "candidate_count": len(candidates),
        "job_count": len(configured_ids),
        "queue_count": len(queue_payload.get("items", [])),
        "unresolved_candidate_count": unresolved_candidates,
        "unresolved_queue_count": unresolved_queue,
        "orphan_queue_count": orphan_queue,
    }


def _backup_file(
    source: Path,
    destination_root: Path,
    relative_name: str,
    manifest_files: dict[str, dict[str, Any]],
) -> None:
    destination = destination_root / relative_name
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)
    manifest_files[relative_name] = {
        "sha256": _sha256_file(destination),
        "size": destination.stat().st_size,
    }


def _referenced_managed_resumes(
    candidates: list[dict[str, Any]],
    root: Path,
) -> tuple[list[tuple[Path, str]], list[str]]:
    files: list[tuple[Path, str]] = []
    issues: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        reference = str(candidate.get("resume_file") or "").strip()
        if not reference or reference in seen:
            continue
        seen.add(reference)
        try:
            resume_path = resolve_managed_resume(
                reference,
                base_dir=root,
                require_exists=True,
            )
        except (ValueError, OSError):
            issues.append(reference)
            continue
        relative = resume_path.relative_to(root).as_posix()
        files.append((resume_path, relative))
    return files, issues


def _create_recovery_point_locked(
    paths: RuntimeDataPaths,
    *,
    reason: str,
    kind: str,
    transaction_id: str,
) -> Path:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_dir = (
        paths.backups_dir
        / ("automatic" if kind == "automatic" else "manual")
        / f"{timestamp}-{transaction_id}"
    )
    backup_dir.mkdir(parents=True, exist_ok=False)
    manifest_files: dict[str, dict[str, Any]] = {}
    for source in (
        paths.job_config,
        paths.candidates,
        paths.contact_queue,
        paths.data_manifest,
    ):
        if source.is_file():
            _backup_file(
                source,
                backup_dir,
                source.name,
                manifest_files,
            )

    try:
        _, candidates, _ = _load_runtime_payloads(paths)
    except (OSError, ValueError, RuntimeError):
        candidates = []
    resume_files, resume_issues = _referenced_managed_resumes(
        candidates,
        paths.root,
    )
    for source, relative in resume_files:
        _backup_file(source, backup_dir, relative, manifest_files)

    manifest = {
        "format_version": BACKUP_FORMAT_VERSION,
        "created_at": _utc_now(),
        "kind": kind,
        "reason": reason,
        "transaction_id": transaction_id,
        "files": manifest_files,
        "resume_issue_count": len(resume_issues),
    }
    _atomic_write_json(backup_dir / "manifest.json", manifest)
    if kind == "automatic":
        _rotate_automatic_backups(paths)
    return backup_dir


def _rotate_automatic_backups(paths: RuntimeDataPaths) -> None:
    automatic_dir = (paths.backups_dir / "automatic").resolve()
    if not automatic_dir.is_dir():
        return
    backups = sorted(
        (
            path
            for path in automatic_dir.iterdir()
            if path.is_dir() and path.parent.resolve() == automatic_dir
        ),
        key=lambda path: path.name,
        reverse=True,
    )
    for expired in backups[AUTOMATIC_BACKUP_RETENTION:]:
        shutil.rmtree(expired)


def _validate_backup_directory(backup_dir: Path) -> dict[str, Any]:
    manifest_path = backup_dir / "manifest.json"
    with open(manifest_path, "r", encoding="utf-8") as file_obj:
        manifest = json.load(file_obj)
    if (
        not isinstance(manifest, dict)
        or manifest.get("format_version") != BACKUP_FORMAT_VERSION
        or not isinstance(manifest.get("files"), dict)
    ):
        raise ValueError("备份清单格式无效")
    for relative, metadata in manifest["files"].items():
        relative_path = Path(relative)
        if relative_path.is_absolute() or ".." in relative_path.parts:
            raise ValueError("备份包含不安全路径")
        file_path = backup_dir / relative_path
        if not file_path.is_file():
            raise ValueError(f"备份缺少文件：{relative}")
        if _sha256_file(file_path) != metadata.get("sha256"):
            raise ValueError(f"备份文件完整性校验失败：{relative}")
        if file_path.stat().st_size != metadata.get("size"):
            raise ValueError(f"备份文件大小校验失败：{relative}")
    return manifest


def _safe_extract_zip(source: Path, destination: Path) -> None:
    with zipfile.ZipFile(source, "r") as archive:
        for info in archive.infolist():
            relative = Path(info.filename)
            if relative.is_absolute() or ".." in relative.parts:
                raise ValueError("备份压缩包包含不安全路径")
            target = (destination / relative).resolve()
            try:
                target.relative_to(destination.resolve())
            except ValueError as exc:
                raise ValueError("备份压缩包包含越界路径") from exc
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(info, "r") as source_file, open(
                target,
                "wb",
            ) as target_file:
                shutil.copyfileobj(source_file, target_file)


@contextmanager
def _materialize_backup(source: str | Path):
    source_path = Path(source)
    if source_path.is_dir():
        yield source_path.resolve()
        return
    if not source_path.is_file() or not zipfile.is_zipfile(source_path):
        raise ValueError("请选择有效的数据备份目录或 ZIP 文件")
    with tempfile.TemporaryDirectory() as tmpdir:
        destination = Path(tmpdir)
        _safe_extract_zip(source_path, destination)
        yield destination


def inspect_backup(source: str | Path) -> dict[str, Any]:
    """Validate a backup and return a privacy-safe restore preview."""
    with _materialize_backup(source) as backup_dir:
        manifest = _validate_backup_directory(backup_dir)
        counts = {
            "candidate_count": 0,
            "job_count": 0,
            "queue_count": 0,
            "resume_count": sum(
                1
                for name in manifest["files"]
                if name.startswith("resumes/")
            ),
        }
        config_path = backup_dir / "job_config.json"
        if config_path.is_file():
            with open(config_path, "r", encoding="utf-8") as file_obj:
                config, _ = upgrade_job_config(json.load(file_obj))
            counts["job_count"] = len(
                _current_job_names_by_uuid(config)
            )
        candidates_path = backup_dir / "candidates_all.json"
        if candidates_path.is_file():
            with open(candidates_path, "r", encoding="utf-8") as file_obj:
                candidates = json.load(file_obj)
            validate_candidates_snapshot(candidates)
            counts["candidate_count"] = len(candidates)
        queue_path = backup_dir / "contact_queue.json"
        if queue_path.is_file():
            with open(queue_path, "r", encoding="utf-8") as file_obj:
                queue_payload = json.load(file_obj)
            validate_contact_queue_snapshot(queue_payload)
            counts["queue_count"] = len(queue_payload.get("items", []))
        return {
            "created_at": str(manifest.get("created_at") or ""),
            "kind": str(manifest.get("kind") or ""),
            "reason": str(manifest.get("reason") or ""),
            **counts,
            "resume_issue_count": int(
                manifest.get("resume_issue_count") or 0
            ),
        }


def _write_staged_files(
    stage_dir: Path,
    files: dict[str, bytes],
) -> dict[str, dict[str, Any]]:
    metadata: dict[str, dict[str, Any]] = {}
    for relative, content in files.items():
        relative_path = _validate_runtime_relative_path(relative)
        stage_path = stage_dir / relative_path
        _atomic_write_bytes(stage_path, content)
        metadata[relative] = {
            "sha256": _sha256_bytes(content),
            "size": len(content),
        }
    return metadata


def _apply_prepared_transaction(
    paths: RuntimeDataPaths,
    journal: dict[str, Any],
    *,
    failure_injector: Callable[[str, str], None] | None = None,
) -> None:
    stage_dir = _require_within(
        Path(journal["stage_dir"]),
        paths.transaction_dir,
        "事务暂存目录",
    )
    for relative, metadata in journal["files"].items():
        _validate_runtime_relative_path(relative)
        stage_path = stage_dir / relative
        if (
            not stage_path.is_file()
            or _sha256_file(stage_path) != metadata["sha256"]
        ):
            raise ValueError(f"事务暂存文件损坏：{relative}")
        target = paths.root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp_target = Path(str(target) + ".transaction.tmp")
        try:
            shutil.copy2(stage_path, tmp_target)
            with open(tmp_target, "r+b") as copied:
                os.fsync(copied.fileno())
            os.replace(tmp_target, target)
            _sync_parent(target)
        finally:
            if tmp_target.exists():
                try:
                    tmp_target.unlink()
                except OSError:
                    pass
        if failure_injector:
            failure_injector("after_replace", relative)

    data_manifest = {
        "version": DATA_MANIFEST_VERSION,
        "generation": journal["transaction_id"],
        "committed_at": _utc_now(),
        "reason": journal["reason"],
        "schemas": {
            "job_config": JOB_CONFIG_SCHEMA_VERSION,
            "candidates": CANDIDATE_SCHEMA_VERSION,
            "contact_queue": CONTACT_QUEUE_SCHEMA_VERSION,
        },
        "files": journal["files"],
    }
    _atomic_write_json(paths.data_manifest, data_manifest)


def _cleanup_transaction(paths: RuntimeDataPaths, journal: dict[str, Any]) -> None:
    raw_stage_dir = str(journal.get("stage_dir") or "").strip()
    if not raw_stage_dir:
        raise ValueError("事务暂存目录缺失")
    stage_dir = _require_within(
        Path(raw_stage_dir),
        paths.transaction_dir,
        "事务暂存目录",
    )
    if stage_dir.is_dir():
        shutil.rmtree(stage_dir)
    try:
        paths.journal.unlink()
    except FileNotFoundError:
        pass


def _commit_runtime_files_locked(
    paths: RuntimeDataPaths,
    files: dict[str, bytes],
    *,
    reason: str,
    failure_injector: Callable[[str, str], None] | None = None,
) -> str:
    transaction_id = uuid.uuid4().hex
    recovery_dir = _create_recovery_point_locked(
        paths,
        reason=reason,
        kind="automatic",
        transaction_id=transaction_id,
    )
    stage_dir = paths.transaction_dir / transaction_id
    stage_dir.mkdir(parents=True, exist_ok=False)
    metadata = _write_staged_files(stage_dir, files)
    absent_targets = [
        relative
        for relative in files
        if not (paths.root / relative).exists()
    ]
    journal = {
        "version": TRANSACTION_JOURNAL_VERSION,
        "status": "prepared",
        "transaction_id": transaction_id,
        "reason": reason,
        "created_at": _utc_now(),
        "stage_dir": str(stage_dir),
        "recovery_dir": str(recovery_dir),
        "absent_targets": absent_targets,
        "files": metadata,
    }
    _atomic_write_json(paths.journal, journal)
    _apply_prepared_transaction(
        paths,
        journal,
        failure_injector=failure_injector,
    )
    journal["status"] = "committed"
    _atomic_write_json(paths.journal, journal)
    _cleanup_transaction(paths, journal)
    return transaction_id


def _restore_recovery_point_locked(
    paths: RuntimeDataPaths,
    journal: dict[str, Any],
) -> None:
    recovery_dir = _require_within(
        Path(journal["recovery_dir"]),
        paths.backups_dir / "automatic",
        "事务恢复点",
    )
    manifest = _validate_backup_directory(recovery_dir)
    for relative in journal.get("absent_targets", []):
        _validate_runtime_relative_path(relative)
        target = paths.root / relative
        if target.exists():
            target.unlink()
    for relative in manifest["files"]:
        if relative.startswith("resumes/") or relative in {
            *_RUNTIME_FILES,
            paths.data_manifest.name,
        }:
            source = recovery_dir / relative
            target = paths.root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            tmp_target = Path(str(target) + ".recovery.tmp")
            shutil.copy2(source, tmp_target)
            os.replace(tmp_target, target)
    _cleanup_transaction(paths, journal)


def recover_pending_transaction(base_dir: str | Path) -> dict[str, Any]:
    """Idempotently finish a prepared transaction or restore its checkpoint."""
    paths = RuntimeDataPaths.from_base_dir(base_dir)
    with _runtime_lock(paths):
        if not paths.journal.is_file():
            return {"recovered": False}
        with open(paths.journal, "r", encoding="utf-8") as file_obj:
            journal = json.load(file_obj)
        if (
            not isinstance(journal, dict)
            or journal.get("version") != TRANSACTION_JOURNAL_VERSION
        ):
            raise RuntimeError("数据事务日志格式无效，禁止继续写入")
        if journal.get("status") == "committed":
            _cleanup_transaction(paths, journal)
            return {
                "recovered": True,
                "action": "cleanup",
                "transaction_id": journal.get("transaction_id"),
            }
        try:
            _apply_prepared_transaction(paths, journal)
        except (OSError, ValueError, KeyError, TypeError):
            _restore_recovery_point_locked(paths, journal)
            return {
                "recovered": True,
                "action": "rollback",
                "transaction_id": journal.get("transaction_id"),
            }
        journal["status"] = "committed"
        _atomic_write_json(paths.journal, journal)
        _cleanup_transaction(paths, journal)
        return {
            "recovered": True,
            "action": "complete",
            "transaction_id": journal.get("transaction_id"),
        }


def ensure_runtime_data_schema(
    base_dir: str | Path,
    *,
    failure_injector: Callable[[str, str], None] | None = None,
) -> dict[str, Any]:
    """Migrate the three identity-coupled JSON files in one transaction."""
    paths = RuntimeDataPaths.from_base_dir(base_dir)
    recover_pending_transaction(paths.root)
    with _runtime_lock(paths):
        config, candidates, queue_payload = _load_runtime_payloads(paths)
        upgraded_config, _ = upgrade_job_config(config)
        migrated_candidates, unresolved_candidates = migrate_candidate_records(
            candidates,
            upgraded_config,
        )
        migrated_queue, unresolved_queue = _upgrade_queue_payload(
            queue_payload,
            upgraded_config,
            migrated_candidates,
        )
        files = {
            "job_config.json": _json_bytes(upgraded_config, indent=4),
            "candidates_all.json": _json_bytes(migrated_candidates),
            "contact_queue.json": _json_bytes(migrated_queue),
        }
        current_bytes = {
            relative: (
                (paths.root / relative).read_bytes()
                if (paths.root / relative).is_file()
                else b""
            )
            for relative in files
        }
        changed = any(
            current_bytes[name].rstrip() != content.rstrip()
            for name, content in files.items()
        )
        if changed:
            transaction_id = _commit_runtime_files_locked(
                paths,
                files,
                reason="数据 Schema 升级",
                failure_injector=failure_injector,
            )
        else:
            transaction_id = ""
        consistency = validate_runtime_consistency(
            upgraded_config,
            migrated_candidates,
            migrated_queue,
        )
        return {
            "changed": changed,
            "transaction_id": transaction_id,
            "unresolved_candidates": unresolved_candidates,
            "unresolved_queue": unresolved_queue,
            **consistency,
        }


def create_backup_package(
    base_dir: str | Path,
    destination: str | Path,
) -> dict[str, Any]:
    """Create a verified local ZIP containing runtime data and managed resumes."""
    paths = RuntimeDataPaths.from_base_dir(base_dir)
    recover_pending_transaction(paths.root)
    destination_path = Path(destination)
    if destination_path.suffix.lower() != ".zip":
        destination_path = destination_path.with_suffix(".zip")
    with _runtime_lock(paths):
        transaction_id = uuid.uuid4().hex
        backup_dir = _create_recovery_point_locked(
            paths,
            reason="用户手动备份",
            kind="manual",
            transaction_id=transaction_id,
        )
        destination_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_zip = Path(str(destination_path) + ".tmp")
        try:
            with zipfile.ZipFile(
                tmp_zip,
                "w",
                compression=zipfile.ZIP_DEFLATED,
            ) as archive:
                for path in sorted(backup_dir.rglob("*")):
                    if path.is_file():
                        archive.write(
                            path,
                            path.relative_to(backup_dir).as_posix(),
                        )
            with open(tmp_zip, "r+b") as zip_file:
                os.fsync(zip_file.fileno())
            preview = inspect_backup(tmp_zip)
            os.replace(tmp_zip, destination_path)
        finally:
            if tmp_zip.exists():
                try:
                    tmp_zip.unlink()
                except OSError:
                    pass
    return {
        "path": str(destination_path),
        "transaction_id": transaction_id,
        **preview,
    }


def restore_backup(
    base_dir: str | Path,
    source: str | Path,
    *,
    failure_injector: Callable[[str, str], None] | None = None,
) -> dict[str, Any]:
    """Validate, migrate, and atomically restore one backup package."""
    paths = RuntimeDataPaths.from_base_dir(base_dir)
    recover_pending_transaction(paths.root)
    with _materialize_backup(source) as backup_dir:
        manifest = _validate_backup_directory(backup_dir)
        config_path = backup_dir / "job_config.json"
        candidates_path = backup_dir / "candidates_all.json"
        queue_path = backup_dir / "contact_queue.json"
        if not config_path.is_file():
            raise ValueError("备份缺少岗位配置")
        with open(config_path, "r", encoding="utf-8") as file_obj:
            config, _ = upgrade_job_config(json.load(file_obj))
        if candidates_path.is_file():
            with open(candidates_path, "r", encoding="utf-8") as file_obj:
                raw_candidates = json.load(file_obj)
        else:
            raw_candidates = []
        candidates, unresolved_candidates = migrate_candidate_records(
            raw_candidates,
            config,
        )
        if queue_path.is_file():
            with open(queue_path, "r", encoding="utf-8") as file_obj:
                raw_queue = json.load(file_obj)
        else:
            raw_queue = {"version": QUEUE_VERSION, "items": []}
        queue_payload, unresolved_queue = _upgrade_queue_payload(
            raw_queue,
            config,
            candidates,
        )
        validate_runtime_consistency(config, candidates, queue_payload)
        files = {
            "job_config.json": _json_bytes(config, indent=4),
            "candidates_all.json": _json_bytes(candidates),
            "contact_queue.json": _json_bytes(queue_payload),
        }
        for relative in manifest["files"]:
            if not relative.startswith("resumes/"):
                continue
            files[relative] = (backup_dir / relative).read_bytes()

        with _runtime_lock(paths):
            transaction_id = _commit_runtime_files_locked(
                paths,
                files,
                reason="从备份恢复数据",
                failure_injector=failure_injector,
            )
    return {
        "restored": True,
        "transaction_id": transaction_id,
        "candidate_count": len(candidates),
        "job_count": len(_current_job_names_by_uuid(config)),
        "queue_count": len(queue_payload.get("items", [])),
        "resume_count": sum(
            1 for name in files if name.startswith("resumes/")
        ),
        "unresolved_candidate_count": len(unresolved_candidates),
        "unresolved_queue_count": len(unresolved_queue),
    }
