"""Persistent user intent for the GUI candidate contact queue."""
from __future__ import annotations

import json
import os
import shutil
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any


QUEUE_VERSION = 1
ACTIVE_STATUSES = frozenset({"待发送", "发送中", "待核实", "发送失败"})
TERMINAL_STATUSES = frozenset({"已发送", "已跳过"})
_QUEUE_FILE_LOCK = threading.RLock()


def candidate_identity(candidate: dict[str, Any]) -> tuple[str, str]:
    """Return the durable candidate/job identity used by the contact queue."""
    return (
        str(candidate.get("geek_id") or "").strip(),
        _normalize_job_name(candidate.get("job_name")),
    )


def build_contact_queue_item(
    candidate: dict[str, Any],
    *,
    source: str = "manual",
    now: str | None = None,
) -> dict[str, Any]:
    """Build one pending queue item around the current candidate object."""
    timestamp = now or datetime.now().strftime("%Y%m%d_%H%M%S")
    return {
        "queue_id": uuid.uuid4().hex,
        "key": candidate_identity(candidate),
        "candidate": candidate,
        "status": "待发送",
        "message": "",
        "source": source,
        "attempts": 0,
        "created_at": timestamp,
        "updated_at": timestamp,
    }


def save_contact_queue(items: list[dict[str, Any]], path: str | Path) -> None:
    """Atomically persist active queue intent without duplicating candidate data."""
    queue_path = Path(path)
    backup_path = Path(str(queue_path) + ".bak")
    payload = {
        "version": QUEUE_VERSION,
        "items": [
            _serialize_item(item)
            for item in items
            if _normalize_status(item.get("status")) in ACTIVE_STATUSES
        ],
    }

    with _QUEUE_FILE_LOCK:
        queue_path.parent.mkdir(parents=True, exist_ok=True)
        if queue_path.exists():
            try:
                shutil.copy2(queue_path, backup_path)
            except OSError:
                pass
        tmp_path = Path(str(queue_path) + ".tmp")
        with open(tmp_path, "w", encoding="utf-8") as file_obj:
            json.dump(payload, file_obj, ensure_ascii=False, indent=2)
        os.replace(tmp_path, queue_path)


def count_pending_contact_queue(items: list[dict[str, Any]]) -> int:
    """Count items that require manual send-result verification."""
    pending = 0
    for item in items:
        if not isinstance(item, dict):
            continue
        candidate = item.get("candidate") or {}
        status = _normalize_status(item.get("status"))
        if candidate.get("greet_confirmation_pending") or status in {"待核实", "发送中"}:
            pending += 1
    return pending


def load_pending_contact_queue_count(path: str | Path) -> int:
    """Read the startup badge count through the queue module's file parser."""
    queue_path = Path(path)
    backup_path = Path(str(queue_path) + ".bak")
    with _QUEUE_FILE_LOCK:
        payload = _load_payload(queue_path)
        if payload is None:
            payload = _load_payload(backup_path)
    if payload is None:
        return 0
    return count_pending_contact_queue(payload.get("items", []))


def load_contact_queue(
    candidates: list[dict[str, Any]],
    path: str | Path,
) -> list[dict[str, Any]]:
    """Restore active queue items and bind them to the latest candidate records."""
    queue_path = Path(path)
    backup_path = Path(str(queue_path) + ".bak")
    with _QUEUE_FILE_LOCK:
        payload = _load_payload(queue_path)
        if payload is None:
            payload = _load_payload(backup_path)
            if payload is not None:
                try:
                    shutil.copy2(backup_path, queue_path)
                except OSError:
                    pass
        if payload is None:
            payload = {"version": QUEUE_VERSION, "items": []}

    candidate_map = {
        candidate_identity(candidate): candidate
        for candidate in candidates
        if candidate_identity(candidate)[0]
    }
    restored: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for raw_item in payload.get("items", []):
        if not isinstance(raw_item, dict):
            continue
        key = (
            str(raw_item.get("geek_id") or "").strip(),
            _normalize_job_name(raw_item.get("job_name")),
        )
        candidate = candidate_map.get(key)
        if not candidate or key in seen or candidate.get("greet_sent"):
            continue

        status = _normalize_status(raw_item.get("status"))
        message = str(raw_item.get("message") or "")
        if candidate.get("greet_confirmation_pending"):
            status = "待核实"
            message = str(
                candidate.get("greet_confirmation_reason")
                or message
                or "上次发送结果需要人工核实"
            )
        elif status == "发送中":
            status = "待核实"
            message = "程序上次在发送过程中退出，请先到 BOSS 沟通列表核实"
        elif status not in ACTIVE_STATUSES or status in TERMINAL_STATUSES:
            continue

        restored.append({
            "queue_id": str(raw_item.get("queue_id") or uuid.uuid4().hex),
            "key": key,
            "candidate": candidate,
            "status": status,
            "message": message,
            "source": str(raw_item.get("source") or "manual"),
            "attempts": _safe_int(raw_item.get("attempts")),
            "created_at": str(raw_item.get("created_at") or ""),
            "updated_at": str(raw_item.get("updated_at") or ""),
        })
        seen.add(key)

    for candidate in candidates:
        key = candidate_identity(candidate)
        if (
            not key[0]
            or key in seen
            or candidate.get("greet_sent")
            or not candidate.get("greet_confirmation_pending")
        ):
            continue
        item = build_contact_queue_item(candidate, source="candidate_state")
        item["status"] = "待核实"
        item["message"] = str(
            candidate.get("greet_confirmation_reason")
            or "上次发送结果需要人工核实"
        )
        restored.append(item)
        seen.add(key)
    return restored


def _serialize_item(item: dict[str, Any]) -> dict[str, Any]:
    candidate = item.get("candidate") or {}
    geek_id, job_name = candidate_identity(candidate)
    if not geek_id:
        raw_key = item.get("key") or ("", "")
        if isinstance(raw_key, (list, tuple)) and len(raw_key) >= 2:
            geek_id = str(raw_key[0] or "").strip()
            job_name = _normalize_job_name(raw_key[1])
    return {
        "queue_id": str(item.get("queue_id") or uuid.uuid4().hex),
        "geek_id": geek_id,
        "job_name": job_name,
        "status": _normalize_status(item.get("status")),
        "message": str(item.get("message") or ""),
        "source": str(item.get("source") or "manual"),
        "attempts": _safe_int(item.get("attempts")),
        "created_at": str(item.get("created_at") or ""),
        "updated_at": str(item.get("updated_at") or ""),
    }


def _load_payload(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        with open(path, "r", encoding="utf-8") as file_obj:
            payload = json.load(file_obj)
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict) or not isinstance(payload.get("items", []), list):
        return None
    return payload


def _normalize_status(value: Any) -> str:
    status = str(value or "待发送")
    legacy = {
        "待确认": "待核实",
        "失败": "发送失败",
        "需人工确认": "已跳过",
    }
    return legacy.get(status, status)


def _normalize_job_name(value: Any) -> str:
    return str(value or "").replace(" ", "")


def _safe_int(value: Any) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0
