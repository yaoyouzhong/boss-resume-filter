"""Candidate persistence helpers for BOSS resume screening."""
from __future__ import annotations

import json
import logging
import os
import shutil
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from candidate_workflow import default_next_followup_at
from constants import SCORE_THRESHOLD_PASS, SCORE_THRESHOLD_RECOMMEND


logger = logging.getLogger(__name__)
_CANDIDATES_FILE_LOCK = threading.RLock()

CANDIDATES_FILE = "candidates_all.json"
_FEEDBACK_FIELDS = (
    'feedback_status',
    'feedback_reasons',
    'feedback_note',
    'feedback_updated_at',
    'followup_status',
    'followup_note',
    'followup_updated_at',
    'next_followup_at',
    'blacklisted',
    'blacklist_reason',
    'blacklisted_at',
    'risk_flags',
    'manual_review_required',
    'auto_greet_blocked_reason',
    'qualification_status',
    'qualification_reasons',
    'qualification_evidence',
    'resume_file',
    'resume_imported_at',
    'resume_eval_adjustment',
    'resume_eval_reason',
    'resume_eval_model',
    'resume_eval_at',
    'greet_context',
    'greet_context_updated_at',
    'greet_sent_at',
    'greet_method',
    'greet_confirmation_pending',
    'greet_confirmation_reason',
    'greet_confirmation_updated_at',
    'contact_approved_at',
    'contact_approval_reason',
    'review_passed_at',
    'review_passed_reasons',
    'review_rejected_at',
    'review_rejected_reasons',
)

# 有时间戳的字段组：(时间戳字段, (关联数据字段...))
# 合并时比较时间戳，取更新的一组值
_TIMESTAMP_FIELD_GROUPS = (
    ('feedback_updated_at', ('feedback_status', 'feedback_reasons', 'feedback_note')),
    ('followup_updated_at', ('followup_status', 'followup_note', 'next_followup_at')),
    ('blacklisted_at', ('blacklisted', 'blacklist_reason')),
    ('greet_context_updated_at', ('greet_context',)),
    ('greet_sent_at', ('greet_sent', 'greet_method')),
    (
        'greet_confirmation_updated_at',
        ('greet_confirmation_pending', 'greet_confirmation_reason'),
    ),
    ('contact_approved_at', ('contact_approval_reason',)),
    ('review_passed_at', ('review_passed_reasons',)),
    ('review_rejected_at', ('review_rejected_reasons',)),
)
_TIMESTAMPED_FIELDS = frozenset(
    f for ts_f, related in _TIMESTAMP_FIELD_GROUPS for f in (ts_f, *related)
)


def _candidate_paths(path: Optional[str] = None) -> tuple[Path, Path]:
    candidate_path = Path(path) if path is not None else Path(CANDIDATES_FILE)
    return candidate_path, Path(str(candidate_path) + ".bak")


def load_candidates_all(path: Optional[str] = None) -> list[dict[str, Any]]:
    """加载候选人数据；主文件损坏时自动尝试从 .bak 恢复。恢复失败时抛出异常，避免静默丢失数据。"""
    with _CANDIDATES_FILE_LOCK:
        candidate_path, backup_path = _candidate_paths(path)
        if candidate_path.exists():
            try:
                with open(candidate_path, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except (json.JSONDecodeError, OSError) as e:
                print(f"加载候选人数据失败：{e}")
                restored = _load_candidates_backup(path)
                if restored is not None:
                    try:
                        shutil.copy2(backup_path, candidate_path)
                        print(f"已从 {backup_path} 恢复候选人数据")
                    except OSError as restore_error:
                        error_msg = f"候选人数据文件损坏且备份恢复失败：{restore_error}"
                        print(error_msg)
                        raise RuntimeError(error_msg) from restore_error
                    return restored
                error_msg = f"候选人数据文件损坏且备份不存在或损坏，数据可能已丢失"
                print(error_msg)
                raise RuntimeError(error_msg)
        restored = _load_candidates_backup(path)
        if restored is not None:
            try:
                shutil.copy2(backup_path, candidate_path)
                print(f"主文件缺失，已从 {backup_path} 恢复候选人数据")
            except OSError as restore_error:
                error_msg = f"主文件缺失且备份恢复失败：{restore_error}"
                print(error_msg)
                raise RuntimeError(error_msg) from restore_error
            return restored
        return []


def _load_candidates_backup(path: Optional[str] = None) -> Optional[list[dict[str, Any]]]:
    """加载备份文件；不存在或损坏时返回 None。"""
    _, backup_path = _candidate_paths(path)
    if not backup_path.exists():
        return None
    try:
        with open(backup_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        print(f"加载候选人备份失败：{e}")
        return None


def get_greeted_geek_ids(candidates_all: list[dict[str, Any]]) -> set[str]:
    """从 candidates_all 中提取已打招呼的 geek_id 集合。"""
    return set(c['geek_id'] for c in candidates_all if c.get('greet_sent') is True)


def candidate_key(geek_id: Any, job_name: str) -> tuple[str, str]:
    """Normalize a (geek_id, job_name) composite key for dedup and lookup."""
    return (str(geek_id), str(job_name).replace(' ', ''))


def get_first_seen(candidate: dict[str, Any], fallback: str = '') -> str:
    """Return the first-seen timestamp with legacy batch_timestamp fallback."""
    return candidate.get('first_seen_at') or candidate.get('batch_timestamp') or fallback


def get_last_evaluated(candidate: dict[str, Any], fallback: str = '') -> str:
    """Return the last-evaluated timestamp with legacy batch_timestamp fallback."""
    return candidate.get('last_evaluated_at') or candidate.get('batch_timestamp') or fallback


def _has_candidate_history(candidate: dict[str, Any]) -> bool:
    """Return whether a rejected candidate has user-owned history to retain."""
    followup_status = str(candidate.get('followup_status') or '')
    return bool(
        candidate.get('feedback_status')
        or candidate.get('blacklisted')
        or candidate.get('greet_sent')
        or candidate.get('greet_confirmation_pending')
        or followup_status not in ('', '未沟通')
        or candidate.get('followup_note')
        or candidate.get('resume_file')
        or candidate.get('resume_eval_adjustment') is not None
        or candidate.get('review_passed_at')
        or candidate.get('review_rejected_at')
    )


def is_recommended_candidate(candidate: dict[str, Any]) -> bool:
    """Return whether a candidate is actionable without manual review blockers."""
    return bool(
        candidate.get('qualification_status', 'qualified') == 'qualified'
        and candidate.get('match_score', 0) >= SCORE_THRESHOLD_RECOMMEND
        and not candidate.get('manual_review_required')
        and not candidate.get('greet_confirmation_pending')
    )


def should_retain_rejected_candidate(candidate: dict[str, Any]) -> bool:
    """Return whether a rejected record still supports review or user history."""
    return bool(
        _has_candidate_history(candidate)
        or candidate.get('qualification_status') == 'manual_review'
        or candidate.get('manual_review_required')
        or candidate.get('llm_evaluated')
        or candidate.get('llm_error')
        or candidate.get('rejection_source') in {
            'previously_recommended',
            'ai_rejected',
            'user_history',
        }
    )


def _is_current_scan_pending(candidate: dict[str, Any]) -> bool:
    """Return whether a record is an ordinary 55-64 point scan snapshot."""
    score = candidate.get('match_score', 0)
    return bool(
        candidate.get('qualification_status') != 'rejected'
        and SCORE_THRESHOLD_PASS <= score < SCORE_THRESHOLD_RECOMMEND
        and candidate.get('qualification_status') != 'manual_review'
        and not candidate.get('manual_review_required')
        and not candidate.get('llm_evaluated')
        and not candidate.get('llm_error')
        and not candidate.get('greet_confirmation_pending')
        and not _has_candidate_history(candidate)
    )


def save_candidates_all(candidates_all: list[dict[str, Any]], path: Optional[str] = None) -> None:
    """保存 candidates_all.json，支持去重、中断恢复和 .bak 备份。"""
    with _CANDIDATES_FILE_LOCK:
        candidate_path, backup_path = _candidate_paths(path)
        unique_candidates = _dedupe_candidates(candidates_all)

        # 兼容旧数据：batch_timestamp 继续表示首次发现时间，避免重复扫描
        # 把历史候选人重新计入”今天/本周”统计。仅在缺少字段时回填。
        for candidate in unique_candidates:
            _normalize_candidate_state_fields(candidate)
            first_seen_at = get_first_seen(candidate)
            if first_seen_at:
                candidate['first_seen_at'] = first_seen_at
                candidate['batch_timestamp'] = first_seen_at
            if not candidate.get('last_evaluated_at') and candidate.get('batch_timestamp'):
                candidate['last_evaluated_at'] = candidate['batch_timestamp']

        # 普通首次淘汰只进入扫描汇总；仅保留有复核或业务价值的淘汰记录。
        retained_candidates = []
        for candidate in unique_candidates:
            if candidate.get('qualification_status') == 'rejected':
                if not should_retain_rejected_candidate(candidate):
                    continue
                candidate = dict(candidate)
                if not candidate.get('review_rejected_at'):
                    candidate['match_score'] = 0
                candidate['recommend_level'] = '未通过'
                retained_candidates.append(candidate)
                continue
            if (
                candidate.get('match_score', 0) >= SCORE_THRESHOLD_PASS
                or _has_candidate_history(candidate)
                or candidate.get('llm_evaluated')
                or candidate.get('llm_error')
            ):
                retained_candidates.append(candidate)
        unique_candidates = retained_candidates

        if candidate_path.exists():
            try:
                shutil.copy2(candidate_path, backup_path)
            except OSError as e:
                print(f"备份候选人数据失败：{e}")

        tmp_file = Path(str(candidate_path) + ".tmp")
        with open(tmp_file, 'w', encoding='utf-8') as f:
            json.dump(unique_candidates, f, ensure_ascii=False, indent=2)
        os.replace(tmp_file, candidate_path)


def mark_candidate_greeted(
    candidate: dict[str, Any],
    method: str,
    timestamp: Optional[str] = None,
) -> None:
    """统一写入打招呼成功状态及审计字段。"""
    greeted_at = timestamp or datetime.now().strftime("%Y%m%d_%H%M%S")
    candidate['greet_sent'] = True
    candidate['greet_sent_at'] = greeted_at
    candidate['greet_method'] = method
    candidate['followup_status'] = "已打招呼"
    candidate['followup_updated_at'] = greeted_at
    candidate['next_followup_at'] = default_next_followup_at("已打招呼", greeted_at)
    candidate.pop('greet_confirmation_pending', None)
    candidate.pop('greet_confirmation_reason', None)
    candidate.pop('greet_confirmation_updated_at', None)


def mark_candidate_greeting_pending(
    candidate: dict[str, Any],
    reason: str,
    timestamp: Optional[str] = None,
) -> None:
    """记录点击已执行但发送结果尚未得到明确确认。"""
    pending_at = timestamp or datetime.now().strftime("%Y%m%d_%H%M%S")
    candidate['greet_sent'] = False
    candidate['greet_confirmation_pending'] = True
    candidate['greet_confirmation_reason'] = reason
    candidate['greet_confirmation_updated_at'] = pending_at


def clear_candidate_greeting_pending(candidate: dict[str, Any]) -> None:
    """Mark a manually verified uncertain greeting as not sent."""
    candidate['greet_sent'] = False
    candidate.pop('greet_confirmation_pending', None)
    candidate.pop('greet_confirmation_reason', None)
    candidate.pop('greet_confirmation_updated_at', None)
    if candidate.get('followup_status') == "已打招呼":
        candidate['followup_status'] = "未沟通"
        candidate['followup_updated_at'] = datetime.now().strftime("%Y%m%d_%H%M%S")
        candidate.pop('next_followup_at', None)


def mark_candidate_not_greeted(
    candidate: dict[str, Any],
    timestamp: Optional[str] = None,
) -> None:
    """Correct an incorrectly recorded greeting and restore an uncontacted state."""
    corrected_at = timestamp or datetime.now().strftime("%Y%m%d_%H%M%S")
    candidate['greet_sent'] = False
    candidate.pop('greet_sent_at', None)
    candidate.pop('greet_method', None)
    candidate.pop('greet_confirmation_pending', None)
    candidate.pop('greet_confirmation_reason', None)
    candidate.pop('greet_confirmation_updated_at', None)
    candidate['followup_status'] = "未沟通"
    candidate['followup_updated_at'] = corrected_at
    candidate.pop('next_followup_at', None)


def merge_candidates_all(
    candidates: list[dict[str, Any]],
    path: Optional[str] = None,
    replace_keys: Optional[set[tuple[str, str]]] = None,
    prune_pending_jobs: Optional[set[str]] = None,
) -> None:
    """合并候选人；完整扫描可替换岗位的普通待定快照。"""
    with _CANDIDATES_FILE_LOCK:
        current = load_candidates_all(path)
        incoming_keys = {
            candidate_key(item.get('geek_id'), item.get('job_name', ''))
            for item in candidates
            if item.get('geek_id')
        } if replace_keys or prune_pending_jobs else set()
        if replace_keys:
            normalized_replace_keys = {
                candidate_key(geek_id, job_name)
                for geek_id, job_name in replace_keys
            }
            refreshed_current = []
            archived_at = datetime.now().strftime("%Y%m%d_%H%M%S")
            for item in current:
                key = candidate_key(item.get('geek_id'), item.get('job_name', ''))
                if key not in normalized_replace_keys or key in incoming_keys:
                    refreshed_current.append(item)
                elif is_recommended_candidate(item) or should_retain_rejected_candidate(item):
                    archived = dict(item)
                    archived['last_evaluated_at'] = archived_at
                    archived['rejected_at'] = archived_at
                    archived['rejection_source'] = (
                        'previously_recommended'
                        if is_recommended_candidate(item)
                        else 'user_history'
                    )
                    archived['match_score'] = 0
                    archived['recommend_level'] = '未通过'
                    archived['qualification_status'] = 'rejected'
                    archived['qualification_reasons'] = ['最新扫描未通过筛选']
                    refreshed_current.append(archived)
            current = refreshed_current
        if prune_pending_jobs:
            normalized_jobs = {str(job).replace(' ', '') for job in prune_pending_jobs}
            current = [
                item for item in current
                if not (
                    str(item.get('job_name', '')).replace(' ', '') in normalized_jobs
                    and _is_current_scan_pending(item)
                    and candidate_key(item.get('geek_id'), item.get('job_name', '')) not in incoming_keys
                )
            ]
        save_candidates_all(current + candidates, path)


def persist_candidate_greeted(
    candidate: dict[str, Any],
    method: str,
    path: Optional[str] = None,
) -> bool:
    """将单个打招呼成功状态立即合并到最新磁盘数据。"""
    if not candidate.get('geek_id'):
        return False
    with _CANDIDATES_FILE_LOCK:
        mark_candidate_greeted(candidate, method)
        merge_candidates_all([candidate], path)
        return True


def persist_candidate_greeting_pending(
    candidate: dict[str, Any],
    reason: str,
    path: Optional[str] = None,
) -> bool:
    """将单个候选人的发送待核实状态立即合并到最新磁盘数据。"""
    if not candidate.get('geek_id'):
        return False
    with _CANDIDATES_FILE_LOCK:
        mark_candidate_greeting_pending(candidate, reason)
        merge_candidates_all([candidate], path)
        return True


def resolve_candidate_greeting_confirmation(
    candidate: dict[str, Any],
    *,
    sent: bool,
    path: Optional[str] = None,
) -> bool:
    """Persist the user's verification of an uncertain greeting result."""
    geek_id = str(candidate.get('geek_id') or '')
    normalized_job = str(candidate.get('job_name') or '').replace(' ', '')
    if not geek_id:
        return False

    with _CANDIDATES_FILE_LOCK:
        candidates = load_candidates_all(path)
        target = next((
            item for item in candidates
            if str(item.get('geek_id') or '') == geek_id
            and str(item.get('job_name') or '').replace(' ', '') == normalized_job
        ), None)
        if target is None:
            return False
        if sent:
            mark_candidate_greeted(target, "manual_confirmed")
        else:
            clear_candidate_greeting_pending(target)
        save_candidates_all(candidates, path)

    for field in (
        'greet_confirmation_pending',
        'greet_confirmation_reason',
        'greet_confirmation_updated_at',
    ):
        candidate.pop(field, None)
    candidate.update(target)
    return True


def update_candidate_greeted(
    geek_id: str,
    job_name: str,
    method: str,
    path: Optional[str] = None,
) -> bool:
    """原子完成候选人读取、打招呼状态更新和保存。"""
    with _CANDIDATES_FILE_LOCK:
        candidates = load_candidates_all(path)
        normalized_job = job_name.replace(" ", "")
        for candidate in candidates:
            if (
                candidate.get('geek_id') == geek_id
                and candidate.get('job_name', '').replace(" ", "") == normalized_job
            ):
                mark_candidate_greeted(candidate, method)
                save_candidates_all(candidates, path)
                return True
        return False


def _merge_manual_fields(target: dict[str, Any], source: dict[str, Any]) -> None:
    """合并人工反馈/跟进/黑名单字段，有时间戳的组取更新的一方。"""
    for ts_field, related in _TIMESTAMP_FIELD_GROUPS:
        t_ts = target.get(ts_field) or ''
        s_ts = source.get(ts_field) or ''
        if s_ts and s_ts > t_ts:
            target[ts_field] = source[ts_field]
            for f in related:
                if source.get(f):
                    target[f] = source[f]
                elif f == 'next_followup_at':
                    target.pop(f, None)
        elif not t_ts:
            # 两边都没有时间戳，回退到 source 有值 target 没值时复制
            for f in related:
                if source.get(f) and not target.get(f):
                    target[f] = source[f]
    # 不在时间戳组内的字段：source 有值 target 没值时复制
    for field in _FEEDBACK_FIELDS:
        if field not in _TIMESTAMPED_FIELDS:
            if source.get(field) and not target.get(field):
                target[field] = source[field]
    _apply_latest_review_outcome(target)


def merge_candidate_business_state(
    candidate: dict[str, Any],
    persisted_candidate: dict[str, Any],
) -> dict[str, Any]:
    """Overlay persisted human and communication state without replacing scan results."""
    merged = dict(candidate)
    _merge_manual_fields(merged, persisted_candidate)
    return merged


def _normalize_candidate_state_fields(candidate: dict[str, Any]) -> None:
    """Normalize legacy qualification flags to one persisted authoritative value."""
    _apply_latest_review_outcome(candidate)
    qualification = str(candidate.get('qualification_status') or '').strip()
    manual_required = bool(candidate.get('manual_review_required'))
    if qualification != 'rejected' and (
        qualification == 'manual_review' or manual_required
    ):
        candidate['qualification_status'] = 'manual_review'
        candidate['manual_review_required'] = True


def _apply_latest_review_outcome(candidate: dict[str, Any]) -> None:
    """Apply the latest explicit human review without masking newly discovered risks."""
    passed_at = str(candidate.get('review_passed_at') or '')
    rejected_at = str(candidate.get('review_rejected_at') or '')
    if not passed_at and not rejected_at:
        return

    if rejected_at and rejected_at >= passed_at:
        candidate.pop('review_passed_at', None)
        candidate.pop('review_passed_reasons', None)
        candidate.pop('contact_approved_at', None)
        candidate.pop('contact_approval_reason', None)
        candidate['qualification_status'] = 'rejected'
        candidate['manual_review_required'] = False
        rejected_reasons = candidate.get('review_rejected_reasons') or []
        if rejected_reasons:
            candidate['qualification_reasons'] = list(rejected_reasons)
        return

    candidate.pop('review_rejected_at', None)
    candidate.pop('review_rejected_reasons', None)
    qualification = str(candidate.get('qualification_status') or 'qualified').strip()
    if qualification != 'manual_review' and not candidate.get('manual_review_required'):
        return
    current_values = candidate.get('qualification_reasons') or []
    if isinstance(current_values, str):
        current_values = [current_values]
    if not current_values and candidate.get('auto_greet_blocked_reason'):
        current_values = [candidate.get('auto_greet_blocked_reason')]
    if not current_values and candidate.get('risk_flags'):
        current_values = candidate.get('risk_flags')
        if isinstance(current_values, str):
            current_values = [current_values]
    passed_values = candidate.get('review_passed_reasons') or []
    if isinstance(passed_values, str):
        passed_values = [passed_values]
    current_reasons = {
        str(reason).strip()
        for reason in current_values
        if str(reason).strip()
    }
    passed_reasons = {
        str(reason).strip()
        for reason in passed_values
        if str(reason).strip()
    }
    if current_reasons and not current_reasons.issubset(passed_reasons):
        return
    candidate['qualification_status'] = 'qualified'
    candidate['manual_review_required'] = False
    candidate['qualification_reasons'] = []
    candidate.pop('auto_greet_blocked_reason', None)


def _should_replace_candidate(
    existing: dict[str, Any], incoming: dict[str, Any]
) -> bool:
    """Choose the latest scan result, with a high-score fallback for legacy data."""
    existing_ts = get_last_evaluated(existing)
    incoming_ts = get_last_evaluated(incoming)
    if existing_ts or incoming_ts:
        return incoming_ts >= existing_ts
    return incoming.get('match_score', 0) > existing.get('match_score', 0)


def _dedupe_candidates(candidates_all: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """按候选人与岗位去重，保留最新评估并合并人工业务状态。"""
    seen: dict[tuple[str, str], dict[str, Any]] = {}

    for c in candidates_all:
        geek_id = c.get('geek_id')
        if geek_id:
            key = candidate_key(geek_id, c.get('job_name', ''))
            if key not in seen:
                seen[key] = dict(c)  # 浅拷贝，避免修改调用方的输入数据
            else:
                old_c = seen[key]
                if _should_replace_candidate(old_c, c) or c.get('greet_sent', False):
                    c = dict(c)
                    first_seen_at = get_first_seen(old_c)
                    last_evaluated_at = get_last_evaluated(c)
                    if first_seen_at:
                        c['first_seen_at'] = first_seen_at
                        c['batch_timestamp'] = first_seen_at
                    if last_evaluated_at:
                        c['last_evaluated_at'] = last_evaluated_at
                    if old_c.get('greet_sent', False) and not c.get('greet_sent', False):
                        c['greet_sent'] = True
                    if old_c.get('greeting_in_progress', False):
                        c['greeting_in_progress'] = True
                    _merge_manual_fields(c, old_c)
                    seen[key] = c
                else:
                    _merge_manual_fields(old_c, c)

    unique_candidates = list(seen.values())

    for c in unique_candidates:
        if c.get('greeting_in_progress') and c.get('greet_sent'):
            del c['greeting_in_progress']
        if c.get('greet_sent'):
            c.pop('greet_confirmation_pending', None)
            c.pop('greet_confirmation_reason', None)
            c.pop('greet_confirmation_updated_at', None)

    return unique_candidates


def is_already_greeted(
    candidates_all: list[dict[str, Any]],
    geek_id: str,
    job_name: Optional[str] = None,
    greeted_index: Optional[set[tuple[str, str]]] = None,
) -> bool:
    """检查是否已打过招呼，支持 (geek_id, job_name) 复合键。

    可通过 greeted_index 参数传入预建的 set[(geek_id, job_name)] 索引，
    避免每次 O(n) 遍历。用 build_greeted_index() 构建。
    """
    if greeted_index is not None:
        if job_name is not None:
            return (geek_id, job_name) in greeted_index
        # 无 job_name 时检查该 geek_id 是否在任何岗位打过招呼
        return any(gid == geek_id for gid, _ in greeted_index)

    for c in candidates_all:
        if c.get('geek_id') == geek_id and c.get('greet_sent') is True:
            if job_name is not None:
                if c.get('job_name', '') == job_name:
                    return True
            else:
                return True
    return False


def build_greeted_index(candidates_all: list[dict[str, Any]]) -> set[tuple[str, str]]:
    """构建 (geek_id, job_name) 打招呼索引，O(n) 一次构建，后续查询 O(1)。"""
    return set(
        (c.get('geek_id'), c.get('job_name', ''))
        for c in candidates_all
        if c.get('geek_id') and c.get('greet_sent') is True
    )


def build_blacklist_index(candidates_all: list[dict[str, Any]]) -> set[str]:
    """构建候选人黑名单索引，按 geek_id 跨岗位生效。"""
    return set(
        str(c.get('geek_id'))
        for c in candidates_all
        if c.get('geek_id') and c.get('blacklisted') is True
    )
