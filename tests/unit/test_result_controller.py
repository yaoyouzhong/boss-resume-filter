import json
import tempfile
from pathlib import Path

from result_controller import (
    ResultController,
    ResultQuery,
    candidate_query_match,
    prepare_result_view,
    result_cache_key,
    result_sort_value,
)


def test_result_view_keeps_full_snapshot_and_stable_metric_scope():
    candidates = [
        {
            "geek_id": "strong",
            "name": "强推",
            "job_name": "Java 工程师",
            "match_score": 80,
            "greet_sent": True,
            "first_seen_at": "20260801_120000",
        },
        {
            "geek_id": "pending",
            "name": "待复核",
            "job_name": "Java 工程师",
            "match_score": 60,
            "first_seen_at": "20260802_120000",
        },
        {
            "geek_id": "other-job",
            "name": "其他岗位",
            "job_name": "Python 工程师",
            "match_score": 70,
            "first_seen_at": "20260802_120000",
        },
        {
            "geek_id": "blocked",
            "name": "已屏蔽",
            "job_name": "Java 工程师",
            "match_score": 90,
            "blacklisted": True,
            "first_seen_at": "20260802_120000",
        },
    ]

    state = prepare_result_view(
        candidates,
        ResultQuery(
            selected_job="Java 工程师",
            date_start="20260801",
            date_end="20260831",
            result_view="待复核",
        ),
    )

    assert len(state.all_candidates) == 4
    assert [item["geek_id"] for item in state.view_candidates] == ["pending"]
    assert state.visible_count == 1
    assert state.metrics.strong == 1
    assert state.metrics.strong_greeted == 1
    assert state.metrics.pending == 1
    assert state.metrics.greeted == 1


def test_result_view_keeps_low_score_ai_context_and_rejected_records():
    candidates = [
        {
            "geek_id": "evaluated",
            "name": "已评估",
            "match_score": 52,
            "llm_evaluated": True,
            "llm_adjustment": -3,
        },
        {
            "geek_id": "failed",
            "name": "评估失败",
            "match_score": 51,
            "llm_error": "超时",
        },
        {
            "geek_id": "rejected",
            "name": "规则淘汰",
            "match_score": 90,
            "qualification_status": "rejected",
        },
    ]

    state = prepare_result_view(
        candidates,
        ResultQuery(result_view="淘汰记录", now=100.0),
    )

    assert state.visible_count == 3
    rows = {row.candidate["geek_id"]: row for row in state.rows}
    assert rows["evaluated"].values[6] == "-3"
    assert rows["failed"].values[6] == "失败"
    assert rows["rejected"].tag == "rejected"


def test_result_view_shows_reference_score_for_rejected_records():
    """淘汰记录行：match_score 固定为 0 时，匹配分列展示 rule_score 参考分。"""
    candidates = [
        {
            "geek_id": "rejected-with-ref",
            "name": "丁小飞",
            "match_score": 0,
            "rule_score": 42,
            "qualification_status": "rejected",
        },
        {
            "geek_id": "rejected-no-ref",
            "name": "旧记录",
            "match_score": 0,
            "qualification_status": "rejected",
        },
        {
            "geek_id": "passed-normal",
            "name": "正常通过",
            "match_score": 72,
            "rule_score": 72,
        },
    ]

    state = prepare_result_view(
        candidates,
        ResultQuery(result_view="淘汰记录", now=100.0),
    )

    rows = {row.candidate["geek_id"]: row for row in state.rows}
    assert rows["rejected-with-ref"].values[5] == 42
    assert rows["rejected-no-ref"].values[5] == 0
    assert [row.candidate["geek_id"] for row in state.rows] == [
        "rejected-with-ref",
        "rejected-no-ref",
    ]
    assert candidate_query_match(candidates[0], ">=40") == "score"
    assert candidate_query_match(candidates[0], ">50") is None
    # 正常候选人的分数列不受参考分逻辑影响
    normal_state = prepare_result_view(candidates, ResultQuery(now=100.0))
    normal_rows = {row.candidate["geek_id"]: row for row in normal_state.rows}
    assert normal_rows["passed-normal"].values[5] == 72


def test_result_view_reports_expired_ai_feedback_without_mutating_input_map():
    feedback = {
        "candidate": {
            "status": "success",
            "message": "评估完成",
            "timestamp": 90.0,
        }
    }
    candidate = {
        "geek_id": "candidate",
        "name": "候选人",
        "match_score": 70,
    }

    state = prepare_result_view(
        [candidate],
        ResultQuery(evaluation_results=feedback, now=100.0),
    )

    assert state.expired_evaluation_ids == ("candidate",)
    assert "candidate" in feedback
    assert state.rows[0].status_display == "未沟通"
    assert "_display_status" not in candidate
    assert "_full_status" not in candidate
    assert "_extra_fields" not in candidate


def test_result_controller_uses_injected_snapshot_loader_and_cache_key():
    with tempfile.TemporaryDirectory() as tmp_dir:
        path = Path(tmp_dir) / "candidates.json"
        path.write_text(json.dumps([{"geek_id": "g1"}]), encoding="utf-8")
        calls = []

        def loader(candidate_path):
            calls.append(candidate_path)
            return [{"geek_id": "g1", "match_score": 70}]

        query = ResultQuery(selected_job="全部岗位")
        state = ResultController(loader).load(path, query)
        key = result_cache_key(path, query)

    assert calls == [path]
    assert state.total_count == 1
    assert key[0] is not None
    assert key[1:] == ("全部岗位", None, None, False, "全部记录")


def test_result_search_and_sort_rules_are_controller_owned():
    candidate = {
        "name": "张三",
        "gender": "女",
        "match_score": 72,
        "recommend_level": "推荐",
    }

    assert candidate_query_match(candidate, "张三") == "exact_name"
    assert candidate_query_match(candidate, "张") == "partial_name"
    assert candidate_query_match(candidate, "女") == "gender"
    assert candidate_query_match(
        candidate,
        "待复核",
        status_display="未沟通｜待复核",
    ) == "status"
    assert candidate_query_match(candidate, ">=70") == "score"
    assert candidate_query_match(candidate, ">80") is None
    assert result_sort_value("salary", "15-25K") == (True, 20.0)
    assert result_sort_value("score", "—") == (False, 0.0)


def test_result_view_keeps_low_score_candidates_with_imported_resume():
    candidates = [
        {
            "geek_id": "ext-low",
            "name": "外部低分",
            "match_score": 25,
            "resume_file": "resumes/abc.txt",
            "source": "external",
        },
        {
            "geek_id": "scan-low",
            "name": "扫描低分",
            "match_score": 25,
        },
    ]

    state = prepare_result_view(
        candidates,
        ResultQuery(result_view="淘汰记录"),
    )

    assert state.visible_count == 1
    assert [row.candidate["geek_id"] for row in state.rows] == ["ext-low"]
