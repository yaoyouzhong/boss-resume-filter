"""storage 模块单元测试 — 覆盖去重、打招呼状态合并、原子写入、备份恢复、
get_greeted_geek_ids、is_already_greeted、build_greeted_index、build_blacklist_index。"""

import contextlib
import io
import json
import os
import tempfile

from storage import (
    _dedupe_candidates,
    build_blacklist_index,
    build_greeted_index,
    get_greeted_geek_ids,
    is_already_greeted,
    load_candidates_all,
    mark_candidate_greeted,
    merge_candidates_all,
    persist_candidate_greeted,
    persist_candidate_greeting_pending,
    resolve_candidate_greeting_confirmation,
    save_candidates_all,
    update_candidate_greeted,
)


def test_resolve_greeting_confirmation_as_sent_clears_pending_and_marks_contacted():
    with tempfile.TemporaryDirectory() as tmpdir:
        path = os.path.join(tmpdir, "candidates.json")
        candidate = {"geek_id": "g1", "job_name": "Java", "match_score": 80}
        save_candidates_all([candidate], path)
        persist_candidate_greeting_pending(candidate, "button unchanged", path)

        assert resolve_candidate_greeting_confirmation(candidate, sent=True, path=path)
        saved = load_candidates_all(path)[0]
        assert saved["greet_sent"] is True
        assert saved["followup_status"] == "已打招呼"
        assert saved["greet_method"] == "manual_confirmed"
        assert "greet_confirmation_pending" not in saved


def test_resolve_greeting_confirmation_as_not_sent_allows_safe_retry():
    with tempfile.TemporaryDirectory() as tmpdir:
        path = os.path.join(tmpdir, "candidates.json")
        candidate = {"geek_id": "g1", "job_name": "Java", "match_score": 80}
        save_candidates_all([candidate], path)
        persist_candidate_greeting_pending(candidate, "button unchanged", path)

        assert resolve_candidate_greeting_confirmation(candidate, sent=False, path=path)
        saved = load_candidates_all(path)[0]
        assert saved["greet_sent"] is False
        assert "greet_confirmation_pending" not in saved
        assert "greet_confirmation_reason" not in saved


# ========== _dedupe_candidates ==========

def test_dedupe_keeps_higher_score():
    result = _dedupe_candidates([
        {"geek_id": "g1", "job_name": "Java", "match_score": 60},
        {"geek_id": "g1", "job_name": "Java", "match_score": 80},
    ])
    assert len(result) == 1
    assert result[0]["match_score"] == 80


def test_dedupe_uses_latest_scan_even_when_score_decreases():
    result = _dedupe_candidates([
        {
            "geek_id": "g1",
            "job_name": "Java",
            "match_score": 80,
            "batch_timestamp": "20260701_100000",
            "feedback_status": "合适",
        },
        {
            "geek_id": "g1",
            "job_name": "Java",
            "match_score": 65,
            "batch_timestamp": "20260702_100000",
            "llm_evaluated": True,
            "llm_adjustment": -5,
        },
    ])

    assert len(result) == 1
    assert result[0]["match_score"] == 65
    assert result[0]["batch_timestamp"] == "20260701_100000"
    assert result[0]["first_seen_at"] == "20260701_100000"
    assert result[0]["last_evaluated_at"] == "20260702_100000"
    assert result[0]["llm_adjustment"] == -5
    assert result[0]["feedback_status"] == "合适"


def test_dedupe_same_batch_keeps_later_ai_result():
    result = _dedupe_candidates([
        {
            "geek_id": "g1",
            "job_name": "Java",
            "match_score": 70,
            "batch_timestamp": "20260702_100000",
            "llm_evaluated": False,
        },
        {
            "geek_id": "g1",
            "job_name": "Java",
            "match_score": 70,
            "batch_timestamp": "20260702_100000",
            "llm_evaluated": True,
            "llm_adjustment": 0,
        },
    ])

    assert result[0]["llm_evaluated"] is True
    assert result[0]["llm_adjustment"] == 0


def test_dedupe_does_not_replace_newer_scan_with_older_snapshot():
    result = _dedupe_candidates([
        {
            "geek_id": "g1",
            "job_name": "Java",
            "match_score": 65,
            "batch_timestamp": "20260702_100000",
        },
        {
            "geek_id": "g1",
            "job_name": "Java",
            "match_score": 90,
            "batch_timestamp": "20260701_100000",
        },
    ])

    assert result[0]["match_score"] == 65
    assert result[0]["batch_timestamp"] == "20260702_100000"


def test_dedupe_different_job_names_not_merged():
    result = _dedupe_candidates([
        {"geek_id": "g1", "job_name": "Java", "match_score": 70},
        {"geek_id": "g1", "job_name": "Python", "match_score": 60},
    ])
    assert len(result) == 2


def test_dedupe_merges_greet_sent_from_old_to_new():
    """old 有 greet_sent=True，new 没有 → 合并后保留 True。"""
    result = _dedupe_candidates([
        {"geek_id": "g1", "job_name": "Java", "match_score": 70, "greet_sent": True},
        {"geek_id": "g1", "job_name": "Java", "match_score": 80},
    ])
    assert len(result) == 1
    assert result[0]["greet_sent"] is True
    assert result[0]["match_score"] == 80


def test_mark_candidate_greeted_writes_status_time_and_method():
    candidate = {"geek_id": "g1", "job_name": "Java", "match_score": 70}

    mark_candidate_greeted(candidate, "auto_list", "20260619_120000")

    assert candidate["greet_sent"] is True
    assert candidate["greet_sent_at"] == "20260619_120000"
    assert candidate["greet_method"] == "auto_list"
    assert candidate["followup_status"] == "已打招呼"
    assert candidate["followup_updated_at"] == "20260619_120000"


def test_update_candidate_greeted_persists_immediately():
    with tempfile.TemporaryDirectory() as tmpdir:
        target = os.path.join(tmpdir, "candidates_all.json")
        save_candidates_all([
            {"geek_id": "g1", "job_name": "Java", "match_score": 70}
        ], target)

        updated = update_candidate_greeted(
            "g1", "Java", "manual_context", target
        )
        loaded = load_candidates_all(target)

    assert updated is True
    assert loaded[0]["greet_sent"] is True
    assert loaded[0]["greet_method"] == "manual_context"
    assert loaded[0]["greet_sent_at"]


def test_persist_candidate_greeted_merges_with_latest_disk_data():
    with tempfile.TemporaryDirectory() as tmpdir:
        target = os.path.join(tmpdir, "candidates_all.json")
        save_candidates_all([
            {"geek_id": "g1", "job_name": "Java", "match_score": 70},
            {"geek_id": "g2", "job_name": "Python", "match_score": 80,
             "feedback_status": "合适"},
        ], target)
        candidate = {
            "geek_id": "g1", "job_name": "Java", "match_score": 70,
            "summary": "完整候选人信息",
        }

        persisted = persist_candidate_greeted(
            candidate, "auto_list", target
        )
        loaded = load_candidates_all(target)

    assert persisted is True
    assert len(loaded) == 2
    assert next(c for c in loaded if c["geek_id"] == "g1")["greet_sent"] is True
    assert next(c for c in loaded if c["geek_id"] == "g2")["feedback_status"] == "合适"


def test_merge_candidates_all_preserves_existing_records():
    with tempfile.TemporaryDirectory() as tmpdir:
        target = os.path.join(tmpdir, "candidates_all.json")
        save_candidates_all([
            {"geek_id": "g1", "job_name": "Java", "match_score": 70}
        ], target)

        merge_candidates_all([
            {"geek_id": "g2", "job_name": "Python", "match_score": 80}
        ], target)
        loaded = load_candidates_all(target)

    assert {c["geek_id"] for c in loaded} == {"g1", "g2"}


def test_merge_candidates_all_updates_latest_scan_and_preserves_feedback():
    with tempfile.TemporaryDirectory() as tmpdir:
        target = os.path.join(tmpdir, "candidates_all.json")
        save_candidates_all([{
            "geek_id": "g1",
            "job_name": "Java",
            "match_score": 80,
            "batch_timestamp": "20260701_100000",
            "feedback_status": "合适",
            "feedback_updated_at": "20260701_120000",
        }], target)

        merge_candidates_all([{
            "geek_id": "g1",
            "job_name": "Java",
            "match_score": 65,
            "batch_timestamp": "20260702_100000",
            "llm_evaluated": True,
            "llm_adjustment": -5,
        }], target)
        loaded = load_candidates_all(target)

    assert len(loaded) == 1
    assert loaded[0]["match_score"] == 65
    assert loaded[0]["batch_timestamp"] == "20260701_100000"
    assert loaded[0]["first_seen_at"] == "20260701_100000"
    assert loaded[0]["last_evaluated_at"] == "20260702_100000"
    assert loaded[0]["llm_adjustment"] == -5
    assert loaded[0]["feedback_status"] == "合适"


def test_dedupe_preserves_feedback_from_old_to_new():
    """高分新记录替换旧记录时，保留旧记录上的人工反馈。"""
    result = _dedupe_candidates([
        {
            "geek_id": "g1",
            "job_name": "Java",
            "match_score": 70,
            "feedback_status": "误推",
            "feedback_reasons": ["技能不匹配", "规则过宽"],
            "feedback_note": "项目深度不足",
            "feedback_updated_at": "20260608_100000",
        },
        {"geek_id": "g1", "job_name": "Java", "match_score": 80},
    ])
    assert len(result) == 1
    assert result[0]["match_score"] == 80
    assert result[0]["feedback_status"] == "误推"
    assert result[0]["feedback_reasons"] == ["技能不匹配", "规则过宽"]
    assert result[0]["feedback_note"] == "项目深度不足"


def test_dedupe_preserves_feedback_from_new_to_old():
    """低分新记录不替换旧记录时，也要把新记录上的人工反馈合并回旧记录。"""
    result = _dedupe_candidates([
        {"geek_id": "g1", "job_name": "Java", "match_score": 80},
        {
            "geek_id": "g1",
            "job_name": "Java",
            "match_score": 70,
            "feedback_status": "合适",
            "feedback_reasons": ["行业经验不符"],
            "feedback_note": "可约面",
            "feedback_updated_at": "20260608_110000",
        },
    ])
    assert len(result) == 1
    assert result[0]["match_score"] == 80
    assert result[0]["feedback_status"] == "合适"
    assert result[0]["feedback_reasons"] == ["行业经验不符"]
    assert result[0]["feedback_note"] == "可约面"


def test_dedupe_preserves_followup_from_old_to_new():
    """高分新记录替换旧记录时，保留旧记录上的跟进状态。"""
    result = _dedupe_candidates([
        {
            "geek_id": "g1",
            "job_name": "Java",
            "match_score": 70,
            "followup_status": "已回复",
            "followup_note": "等候选人确认时间",
            "followup_updated_at": "20260608_120000",
        },
        {"geek_id": "g1", "job_name": "Java", "match_score": 80},
    ])
    assert len(result) == 1
    assert result[0]["match_score"] == 80
    assert result[0]["followup_status"] == "已回复"
    assert result[0]["followup_note"] == "等候选人确认时间"


def test_dedupe_preserves_followup_from_new_to_old():
    """低分新记录不替换旧记录时，也要把新记录上的跟进状态合并回旧记录。"""
    result = _dedupe_candidates([
        {"geek_id": "g1", "job_name": "Java", "match_score": 80},
        {
            "geek_id": "g1",
            "job_name": "Java",
            "match_score": 70,
            "followup_status": "待约面",
            "followup_note": "周三沟通",
            "followup_updated_at": "20260608_130000",
        },
    ])
    assert len(result) == 1
    assert result[0]["match_score"] == 80
    assert result[0]["followup_status"] == "待约面"
    assert result[0]["followup_note"] == "周三沟通"


def test_dedupe_preserves_greeting_pending_and_success_clears_it():
    pending = {
        "geek_id": "g1",
        "job_name": "Java",
        "match_score": 70,
        "greet_confirmation_pending": True,
        "greet_confirmation_reason": "按钮未变化",
        "greet_confirmation_updated_at": "20260622_100000",
    }
    result = _dedupe_candidates([
        pending,
        {"geek_id": "g1", "job_name": "Java", "match_score": 80},
    ])
    assert result[0]["greet_confirmation_pending"] is True

    greeted = dict(result[0])
    mark_candidate_greeted(greeted, "auto_list", "20260622_100100")
    result = _dedupe_candidates([pending, greeted])
    assert result[0]["greet_sent"] is True
    assert "greet_confirmation_pending" not in result[0]


def test_dedupe_preserves_blacklist_from_old_to_new():
    """高分新记录替换旧记录时，保留旧记录上的黑名单状态。"""
    result = _dedupe_candidates([
        {
            "geek_id": "g1",
            "job_name": "Java",
            "match_score": 70,
            "blacklisted": True,
            "blacklist_reason": "面试未通过",
            "blacklisted_at": "20260612_100000",
        },
        {"geek_id": "g1", "job_name": "Java", "match_score": 80},
    ])
    assert len(result) == 1
    assert result[0]["match_score"] == 80
    assert result[0]["blacklisted"] is True
    assert result[0]["blacklist_reason"] == "面试未通过"


def test_dedupe_preserves_blacklist_from_new_to_old():
    """低分新记录不替换旧记录时，也要把黑名单状态合并回旧记录。"""
    result = _dedupe_candidates([
        {"geek_id": "g1", "job_name": "Java", "match_score": 80},
        {
            "geek_id": "g1",
            "job_name": "Java",
            "match_score": 70,
            "blacklisted": True,
            "blacklist_reason": "性格不匹配",
            "blacklisted_at": "20260612_110000",
        },
    ])
    assert len(result) == 1
    assert result[0]["match_score"] == 80
    assert result[0]["blacklisted"] is True
    assert result[0]["blacklist_reason"] == "性格不匹配"


def test_dedupe_preserves_greet_context_from_old_to_new():
    """高分新记录替换旧记录时，保留详情页打招呼上下文。"""
    context = {"chat_start": {"jid": "jid123", "lid": "lid123", "securityId": "sec123"}}
    result = _dedupe_candidates([
        {
            "geek_id": "g1",
            "job_name": "Java",
            "match_score": 70,
            "greet_context": context,
            "greet_context_updated_at": "2026-06-17T10:00:00",
        },
        {"geek_id": "g1", "job_name": "Java", "match_score": 80},
    ])
    assert len(result) == 1
    assert result[0]["match_score"] == 80
    assert result[0]["greet_context"] == context
    assert result[0]["greet_context_updated_at"] == "2026-06-17T10:00:00"


def test_dedupe_merges_greet_sent_from_new_to_old():
    """new 有 greet_sent=True → 直接替换。"""
    result = _dedupe_candidates([
        {"geek_id": "g1", "job_name": "Java", "match_score": 80},
        {"geek_id": "g1", "job_name": "Java", "match_score": 70, "greet_sent": True},
    ])
    assert len(result) == 1
    assert result[0]["greet_sent"] is True


def test_dedupe_clears_greeting_in_progress_when_greeted():
    """greeting_in_progress + greet_sent → 清除 greeting_in_progress。"""
    result = _dedupe_candidates([
        {"geek_id": "g1", "job_name": "Java", "match_score": 70,
         "greet_sent": True, "greeting_in_progress": True},
    ])
    assert len(result) == 1
    assert result[0]["greet_sent"] is True
    assert "greeting_in_progress" not in result[0]


def test_dedupe_keeps_greeting_in_progress_when_not_greeted():
    """只有 greeting_in_progress、没有 greet_sent → 保留。"""
    result = _dedupe_candidates([
        {"geek_id": "g1", "job_name": "Java", "match_score": 70,
         "greeting_in_progress": True},
    ])
    assert len(result) == 1
    assert result[0]["greeting_in_progress"] is True


def test_dedupe_no_geek_id_skipped():
    """没有 geek_id 的记录不参与去重（被丢弃）。"""
    result = _dedupe_candidates([
        {"job_name": "Java", "match_score": 70},
        {"geek_id": "g1", "job_name": "Java", "match_score": 60},
    ])
    assert len(result) == 1
    assert result[0]["geek_id"] == "g1"


def test_dedupe_empty_job_name_defaults_to_empty_string():
    result = _dedupe_candidates([
        {"geek_id": "g1", "match_score": 60},
        {"geek_id": "g1", "match_score": 70},
    ])
    assert len(result) == 1
    assert result[0]["match_score"] == 70


def test_dedupe_empty_list():
    assert _dedupe_candidates([]) == []


# ========== get_greeted_geek_ids ==========

def test_get_greeted_geek_ids_extracts_greeted():
    candidates = [
        {"geek_id": "g1", "greet_sent": True},
        {"geek_id": "g2", "greet_sent": False},
        {"geek_id": "g3", "greet_sent": True},
        {"geek_id": "g4"},
    ]
    assert get_greeted_geek_ids(candidates) == {"g1", "g3"}


def test_get_greeted_geek_ids_empty():
    assert get_greeted_geek_ids([]) == set()


# ========== is_already_greeted ==========

def test_is_already_greeted_by_geek_id_and_job():
    candidates = [
        {"geek_id": "g1", "job_name": "Java", "greet_sent": True},
        {"geek_id": "g1", "job_name": "Python", "greet_sent": False},
    ]
    assert is_already_greeted(candidates, "g1", "Java") is True
    assert is_already_greeted(candidates, "g1", "Python") is False
    assert is_already_greeted(candidates, "g2", "Java") is False


def test_is_already_greeted_without_job_name():
    """无 job_name 时，只要该 geek_id 在任何岗位打过招呼即返回 True。"""
    candidates = [
        {"geek_id": "g1", "job_name": "Java", "greet_sent": True},
    ]
    assert is_already_greeted(candidates, "g1") is True
    assert is_already_greeted(candidates, "g2") is False


def test_is_already_greeted_with_index():
    candidates = [
        {"geek_id": "g1", "job_name": "Java", "greet_sent": True},
        {"geek_id": "g2", "job_name": "Python", "greet_sent": False},
    ]
    index = build_greeted_index(candidates)
    assert is_already_greeted(candidates, "g1", "Java", index) is True
    assert is_already_greeted(candidates, "g2", "Python", index) is False


def test_is_already_greeted_index_without_job_name():
    candidates = [
        {"geek_id": "g1", "job_name": "Java", "greet_sent": True},
    ]
    index = build_greeted_index(candidates)
    assert is_already_greeted(candidates, "g1", greeted_index=index) is True
    assert is_already_greeted(candidates, "g9", greeted_index=index) is False


# ========== build_greeted_index ==========

def test_build_greeted_index_only_greeted():
    candidates = [
        {"geek_id": "g1", "job_name": "Java", "greet_sent": True},
        {"geek_id": "g2", "job_name": "Python", "greet_sent": False},
        {"geek_id": "g3", "job_name": "Go", "greet_sent": True},
    ]
    index = build_greeted_index(candidates)
    assert ("g1", "Java") in index
    assert ("g3", "Go") in index
    assert ("g2", "Python") not in index


def test_build_greeted_index_skips_no_geek_id():
    candidates = [
        {"job_name": "Java", "greet_sent": True},
        {"geek_id": "g1", "job_name": "Java", "greet_sent": True},
    ]
    index = build_greeted_index(candidates)
    assert len(index) == 1
    assert ("g1", "Java") in index


# ========== build_blacklist_index ==========

def test_build_blacklist_index_is_cross_job_by_geek_id():
    candidates = [
        {"geek_id": "g1", "job_name": "Java", "blacklisted": True},
        {"geek_id": "g1", "job_name": "Python"},
        {"geek_id": "g2", "job_name": "Java", "blacklisted": False},
        {"geek_id": "g3", "job_name": "Go", "blacklisted": True},
    ]
    assert build_blacklist_index(candidates) == {"g1", "g3"}


# ========== save_candidates_all 原子写入 ==========

def test_save_creates_backup_of_existing_file():
    with tempfile.TemporaryDirectory() as tmpdir:
        target = os.path.join(tmpdir, "candidates_all.json")
        # 先写入初始数据
        with open(target, "w", encoding="utf-8") as f:
            json.dump([{"geek_id": "old", "job_name": "Java"}], f)

        with contextlib.redirect_stdout(io.StringIO()):
            save_candidates_all([
                {"geek_id": "g1", "job_name": "Java", "match_score": 70},
            ], target)

        # 备份文件应存在且包含旧数据
        bak = target + ".bak"
        assert os.path.exists(bak)
        with open(bak, "r", encoding="utf-8") as f:
            backup = json.load(f)
        assert backup[0]["geek_id"] == "old"


def test_save_no_tmp_file_left_behind():
    """原子写入完成后 .tmp 文件不应残留。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        target = os.path.join(tmpdir, "candidates_all.json")
        with contextlib.redirect_stdout(io.StringIO()):
            save_candidates_all([
                {"geek_id": "g1", "job_name": "Java", "match_score": 70},
            ], target)
        tmp = target + ".tmp"
        assert not os.path.exists(tmp)


def test_save_no_backup_when_no_existing_file():
    """首次保存（无现有文件）不应产生 .bak。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        target = os.path.join(tmpdir, "candidates_all.json")
        with contextlib.redirect_stdout(io.StringIO()):
            save_candidates_all([
                {"geek_id": "g1", "job_name": "Java", "match_score": 70},
            ], target)
        bak = target + ".bak"
        assert not os.path.exists(bak)


# ========== load_candidates_all ==========

def test_load_returns_empty_when_no_file():
    """首次运行没有候选人文件时应视为空数据，而不是数据损坏。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        target = os.path.join(tmpdir, "candidates_all.json")
        with contextlib.redirect_stdout(io.StringIO()):
            result = load_candidates_all(target)
    assert result == []


def test_load_restores_when_main_file_missing_but_backup_exists():
    """主文件被误删但备份仍在时，应从备份恢复。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        target = os.path.join(tmpdir, "candidates_all.json")
        backup_data = [{"geek_id": "g1", "job_name": "Java"}]
        with open(target + ".bak", "w", encoding="utf-8") as f:
            json.dump(backup_data, f)

        with contextlib.redirect_stdout(io.StringIO()):
            result = load_candidates_all(target)

        assert result == backup_data
        assert os.path.exists(target)


def test_load_returns_empty_when_both_files_corrupt():
    with tempfile.TemporaryDirectory() as tmpdir:
        target = os.path.join(tmpdir, "candidates_all.json")
        with open(target, "w") as f:
            f.write("{broken")
        with open(target + ".bak", "w") as f:
            f.write("also{broken")
        with contextlib.redirect_stdout(io.StringIO()):
            try:
                load_candidates_all(target)
                assert False, "Expected RuntimeError"
            except RuntimeError:
                pass


def test_load_restores_backup_and_copies_to_main():
    with tempfile.TemporaryDirectory() as tmpdir:
        target = os.path.join(tmpdir, "candidates_all.json")
        backup_data = [{"geek_id": "g1", "job_name": "Java"}]
        with open(target, "w") as f:
            f.write("{broken")
        with open(target + ".bak", "w", encoding="utf-8") as f:
            json.dump(backup_data, f)

        with contextlib.redirect_stdout(io.StringIO()):
            result = load_candidates_all(target)

        assert result == backup_data
        # 恢复后备份应被复制到主文件
        with open(target, "r", encoding="utf-8") as f:
            restored = json.load(f)
        assert restored == backup_data


# ========== save + load 集成 ==========

def test_save_then_load_roundtrip():
    with tempfile.TemporaryDirectory() as tmpdir:
        target = os.path.join(tmpdir, "candidates_all.json")
        data = [
            {"geek_id": "g1", "job_name": "Java", "match_score": 80, "greet_sent": True},
            {"geek_id": "g2", "job_name": "Python", "match_score": 65},
        ]
        with contextlib.redirect_stdout(io.StringIO()):
            save_candidates_all(data, target)
            loaded = load_candidates_all(target)
    assert len(loaded) == 2
    assert loaded[0]["greet_sent"] is True


def test_save_drops_ordinary_rejected_candidate_without_review_value():
    with tempfile.TemporaryDirectory() as tmpdir:
        target = os.path.join(tmpdir, "candidates_all.json")
        save_candidates_all([{
            "geek_id": "rejected",
            "job_name": "Java",
            "match_score": 90,
            "qualification_status": "rejected",
        }], target)
        assert load_candidates_all(target) == []


def test_save_keeps_rejected_candidate_history_outside_active_score():
    with tempfile.TemporaryDirectory() as tmpdir:
        target = os.path.join(tmpdir, "candidates_all.json")
        save_candidates_all([{
            "geek_id": "rejected-with-history",
            "job_name": "Java",
            "match_score": 90,
            "qualification_status": "rejected",
            "feedback_status": "误推",
        }], target)
        loaded = load_candidates_all(target)

    assert len(loaded) == 1
    assert loaded[0]["match_score"] == 0
    assert loaded[0]["recommend_level"] == "未通过"
    assert loaded[0]["qualification_status"] == "rejected"
    assert loaded[0]["feedback_status"] == "误推"


def test_save_keeps_rejected_candidate_that_required_manual_review():
    with tempfile.TemporaryDirectory() as tmpdir:
        target = os.path.join(tmpdir, "candidates_all.json")
        save_candidates_all([{
            "geek_id": "manual-rejected",
            "job_name": "Java",
            "match_score": 0,
            "qualification_status": "rejected",
            "manual_review_required": True,
        }], target)
        loaded = load_candidates_all(target)

    assert len(loaded) == 1
    assert loaded[0]["geek_id"] == "manual-rejected"


def test_merge_candidates_all_archives_previous_pass_when_latest_scan_fails():
    with tempfile.TemporaryDirectory() as tmpdir:
        target = os.path.join(tmpdir, "candidates_all.json")
        save_candidates_all([{
            "geek_id": "g1",
            "job_name": "Java",
            "match_score": 80,
            "batch_timestamp": "20260701_100000",
        }], target)

        merge_candidates_all([], target, replace_keys={("g1", "Java")})

        loaded = load_candidates_all(target)

    assert len(loaded) == 1
    assert loaded[0]["qualification_status"] == "rejected"
    assert loaded[0]["qualification_reasons"] == ["最新扫描未通过筛选"]
    assert loaded[0]["first_seen_at"] == "20260701_100000"
    assert loaded[0]["rejection_source"] == "previously_recommended"


def test_complete_scan_replaces_old_plain_pending_but_keeps_review_work():
    with tempfile.TemporaryDirectory() as tmpdir:
        target = os.path.join(tmpdir, "candidates_all.json")
        save_candidates_all([
            {"geek_id": "old-pending", "job_name": "Java", "match_score": 60},
            {"geek_id": "manual", "job_name": "Java", "match_score": 60, "manual_review_required": True},
            {"geek_id": "other-job", "job_name": "Python", "match_score": 60},
        ], target)

        merge_candidates_all(
            [{"geek_id": "new-pending", "job_name": "Java", "match_score": 61}],
            target,
            prune_pending_jobs={"Java"},
        )
        loaded = load_candidates_all(target)

    assert {c["geek_id"] for c in loaded} == {"new-pending", "manual", "other-job"}


def test_partial_scan_keeps_old_plain_pending_snapshot():
    with tempfile.TemporaryDirectory() as tmpdir:
        target = os.path.join(tmpdir, "candidates_all.json")
        save_candidates_all([
            {"geek_id": "old-pending", "job_name": "Java", "match_score": 60},
        ], target)

        merge_candidates_all(
            [{"geek_id": "new-pending", "job_name": "Java", "match_score": 61}],
            target,
        )
        loaded = load_candidates_all(target)

    assert {c["geek_id"] for c in loaded} == {"old-pending", "new-pending"}


def test_merge_candidates_all_archives_failed_candidate_with_feedback_only():
    with tempfile.TemporaryDirectory() as tmpdir:
        target = os.path.join(tmpdir, "candidates_all.json")
        save_candidates_all([
            {
                "geek_id": "g1",
                "job_name": "Java",
                "match_score": 80,
                "feedback_status": "误推",
            },
            {
                "geek_id": "g1",
                "job_name": "Python",
                "match_score": 75,
            },
        ], target)

        merge_candidates_all([], target, replace_keys={("g1", "Java")})
        loaded = load_candidates_all(target)

    assert len(loaded) == 2
    java_record = next(c for c in loaded if c["job_name"] == "Java")
    assert java_record["match_score"] == 0
    assert java_record["qualification_status"] == "rejected"
    assert java_record["qualification_reasons"] == ["最新扫描未通过筛选"]
    assert java_record["feedback_status"] == "误推"
    assert next(c for c in loaded if c["job_name"] == "Python")["match_score"] == 75


def test_manual_review_candidate_is_not_archived_as_previously_recommended():
    with tempfile.TemporaryDirectory() as tmpdir:
        target = os.path.join(tmpdir, "candidates_all.json")
        save_candidates_all([{
            "geek_id": "manual-high",
            "job_name": "Java",
            "match_score": 72,
            "qualification_status": "manual_review",
            "manual_review_required": True,
        }], target)

        merge_candidates_all([], target, replace_keys={("manual-high", "Java")})
        loaded = load_candidates_all(target)

    assert len(loaded) == 1
    assert loaded[0]["rejection_source"] == "user_history"


def test_save_keeps_ai_evaluated_candidate_below_pass_score():
    with tempfile.TemporaryDirectory() as tmpdir:
        target = os.path.join(tmpdir, "candidates_all.json")
        save_candidates_all([{
            "geek_id": "ai-low",
            "job_name": "Java",
            "match_score": 53,
            "rule_score": 56,
            "llm_evaluated": True,
            "llm_adjustment": -3,
        }], target)
        loaded = load_candidates_all(target)
        assert len(loaded) == 1
        assert loaded[0]["geek_id"] == "ai-low"
        assert loaded[0]["match_score"] == 53
        assert loaded[0]["llm_adjustment"] == -3


def test_save_keeps_failed_ai_candidate_below_pass_score_for_retry():
    with tempfile.TemporaryDirectory() as tmpdir:
        target = os.path.join(tmpdir, "candidates_all.json")
        save_candidates_all([{
            "geek_id": "ai-failed-low",
            "job_name": "Java",
            "match_score": 53,
            "llm_evaluated": False,
            "llm_error": "请求超时",
        }], target)
        loaded = load_candidates_all(target)
        assert len(loaded) == 1
        assert loaded[0]["geek_id"] == "ai-failed-low"
        assert loaded[0]["llm_error"] == "请求超时"


# ========== load_candidates_all 边界场景 ==========

def test_load_happy_path_from_valid_file():
    """主文件正常时应直接加载，不触发备份恢复。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        target = os.path.join(tmpdir, "candidates_all.json")
        data = [{"geek_id": "g1", "job_name": "Java", "match_score": 70}]
        with open(target, "w", encoding="utf-8") as f:
            json.dump(data, f)

        result = load_candidates_all(target)
    assert len(result) == 1
    assert result[0]["geek_id"] == "g1"


def test_load_backup_corrupted_returns_empty():
    """主文件损坏且备份也损坏时应抛出异常。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        target = os.path.join(tmpdir, "candidates_all.json")
        with open(target, "w") as f:
            f.write("{broken")
        with open(target + ".bak", "w") as f:
            f.write("also{broken")
        with contextlib.redirect_stdout(io.StringIO()):
            try:
                load_candidates_all(target)
                assert False, "Expected RuntimeError"
            except RuntimeError:
                pass


# ========== save_candidates_all 边界场景 ==========

def test_save_backup_failure_still_saves():
    """备份创建失败时，保存操作本身不应中断。"""
    import unittest.mock as mock
    with tempfile.TemporaryDirectory() as tmpdir:
        target = os.path.join(tmpdir, "candidates_all.json")
        # 先创建一个文件，这样 save 会尝试备份
        with open(target, "w", encoding="utf-8") as f:
            json.dump([{"geek_id": "old"}], f)

        with mock.patch('storage.shutil.copy2', side_effect=OSError("disk full")):
            with contextlib.redirect_stdout(io.StringIO()):
                save_candidates_all([
                    {"geek_id": "g1", "job_name": "Java", "match_score": 70},
                ], target)

        # 数据应仍然保存成功
        with open(target, "r", encoding="utf-8") as f:
            saved = json.load(f)
        assert len(saved) == 1
        assert saved[0]["geek_id"] == "g1"


def test_save_with_explicit_path_creates_backup():
    """显式路径保存时，备份也应在同一目录下。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        target = os.path.join(tmpdir, "nested", "candidates.json")
        os.makedirs(os.path.dirname(target))
        # 先写入旧数据
        with open(target, "w", encoding="utf-8") as f:
            json.dump([{"geek_id": "old"}], f)

        with contextlib.redirect_stdout(io.StringIO()):
            save_candidates_all([
                {"geek_id": "g1", "job_name": "Java", "match_score": 70},
            ], target)

        bak = target + ".bak"
        assert os.path.exists(bak)
        with open(bak, "r", encoding="utf-8") as f:
            backup = json.load(f)
        assert backup[0]["geek_id"] == "old"
