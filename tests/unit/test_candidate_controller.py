import tempfile
from pathlib import Path
from types import SimpleNamespace

from candidate_controller import CandidateController, CandidatePersistence


class _MemoryPersistence:
    def __init__(self, records):
        self.records = records

    def update_records(self, predicate, mutate, _path, update_all=False):
        updated = 0
        for record in self.records:
            if not predicate(record):
                continue
            mutate(record)
            updated += 1
            if not update_all:
                break
        return updated

    def mutate_all(self, mutate, _path):
        return mutate(self.records)

    def mutate_with_resume_cleanup(self, mutate, _path, **_kwargs):
        return mutate(self.records), None

    def remove_with_resume_cleanup(self, predicate, _path, **_kwargs):
        kept = [record for record in self.records if not predicate(record)]
        removed = len(self.records) - len(kept)
        self.records[:] = kept
        return removed, None

    @staticmethod
    def mark_greeted(candidate, method, timestamp):
        candidate["greet_sent"] = True
        candidate["greet_method"] = method
        candidate["greet_time"] = timestamp

    @staticmethod
    def mark_not_greeted(candidate, _timestamp):
        candidate["greet_sent"] = False
        candidate.pop("greet_method", None)
        candidate.pop("greet_time", None)


def _controller(records):
    temp_dir = tempfile.TemporaryDirectory()
    path = Path(temp_dir.name) / "candidates.json"
    path.touch()
    backend = _MemoryPersistence(records)
    controller = CandidateController(
        path,
        Path(temp_dir.name),
        CandidatePersistence(
            update_records=backend.update_records,
            mutate_all=backend.mutate_all,
            mutate_with_resume_cleanup=backend.mutate_with_resume_cleanup,
            remove_with_resume_cleanup=backend.remove_with_resume_cleanup,
            mark_greeted=backend.mark_greeted,
            mark_not_greeted=backend.mark_not_greeted,
        ),
    )
    return temp_dir, backend, controller


def test_followup_updates_only_matching_candidate_job_and_active_snapshot():
    records = [
        {"geek_id": "g1", "job_name": "Java", "followup_status": "未沟通"},
        {"geek_id": "g1", "job_name": "Python", "followup_status": "未沟通"},
    ]
    active = dict(records[0])
    temp_dir, _backend, controller = _controller(records)
    try:
        updated = controller.update_followup(
            "g1",
            "Java",
            "已回复",
            "电话沟通",
            "20260811_090000",
            "20260810_090000",
            candidate=active,
        )
    finally:
        temp_dir.cleanup()

    assert updated is True
    assert records[0]["followup_status"] == "已回复"
    assert records[0]["greet_sent"] is True
    assert records[1]["followup_status"] == "未沟通"
    assert active["next_followup_at"] == "20260811_090000"


def test_review_pass_and_rejection_keep_contact_approval_semantics():
    records = [
        {
            "geek_id": "g1",
            "job_name": "Java",
            "match_score": 60,
            "manual_review_required": True,
            "qualification_status": "manual_review",
        },
        {
            "geek_id": "g2",
            "job_name": "Java",
            "match_score": 60,
            "manual_review_required": True,
            "qualification_status": "manual_review",
            "contact_approved_at": "old",
        },
    ]
    temp_dir, _backend, controller = _controller(records)
    try:
        passed = controller.complete_review(
            "g1",
            "Java",
            contact_approval_reason="人工确认可联系",
            timestamp="20260810_100000",
        )
        rejected = controller.reject_review(
            "g2",
            "Java",
            review_rejected_reasons=["经验待确认后不通过"],
            timestamp="20260810_110000",
        )
    finally:
        temp_dir.cleanup()

    assert passed == 1
    assert records[0]["qualification_status"] == "qualified"
    assert records[0]["contact_approval_reason"] == "人工确认可联系"
    assert rejected == 1
    assert records[1]["qualification_status"] == "rejected"
    assert records[1]["review_rejected_reasons"] == ["经验待确认后不通过"]
    assert "contact_approved_at" not in records[1]


def test_feedback_records_low_score_review_pass_and_clears_negative_approval():
    records = [
        {"geek_id": "g1", "job_name": "Java", "match_score": 60},
        {
            "geek_id": "g2",
            "job_name": "Java",
            "match_score": 80,
            "contact_approved_at": "old",
            "contact_approval_reason": "old",
        },
    ]
    temp_dir, _backend, controller = _controller(records)
    try:
        assert controller.update_feedback(
            "g1", "Java", "合适", ["规则过窄"], "确认", timestamp="t1"
        )
        assert controller.update_feedback(
            "g2", "Java", "放弃", ["薪资不合适"], "", timestamp="t2"
        )
    finally:
        temp_dir.cleanup()

    assert records[0]["review_passed_at"] == "t1"
    assert records[0]["review_passed_reasons"] == ["评分处于待定区间（60 分）"]
    assert records[1]["feedback_status"] == "放弃"
    assert "contact_approved_at" not in records[1]


def test_blacklist_applies_across_jobs_and_unblacklist_preserves_followup():
    records = [
        {"geek_id": "g1", "job_name": "Java", "followup_status": "未沟通"},
        {"geek_id": "g1", "job_name": "Python", "followup_status": "已归档"},
    ]
    temp_dir, _backend, controller = _controller(records)
    try:
        assert controller.blacklist("g1", "重复候选人", timestamp="t1") == 2
        assert controller.unblacklist("g1") == 2
    finally:
        temp_dir.cleanup()

    assert records[0]["followup_status"] == "不合适"
    assert records[1]["followup_status"] == "已归档"
    assert all("blacklisted" not in record for record in records)


def test_ai_evaluation_merge_uses_candidate_and_job_composite_identity():
    records = [
        {"geek_id": "g1", "job_name": "Java", "match_score": 60},
        {"geek_id": "g1", "job_name": "Python", "match_score": 60},
    ]
    temp_dir, _backend, controller = _controller(records)
    try:
        result_map = controller.save_ai_evaluations(
            [{
                "geek_id": "g1",
                "job_name": "Java",
                "match_score": 72,
                "llm_evaluated": True,
            }]
        )
    finally:
        temp_dir.cleanup()

    assert CandidateController.identity(records[0]) in result_map
    assert records[0]["match_score"] == 72
    assert records[0]["llm_evaluated"] is True
    assert records[1]["match_score"] == 60


def test_resume_import_uses_explicit_services_and_refreshes_active_candidate():
    records = [{"geek_id": "g1", "job_name": "Java", "name": "旧名称"}]
    active = dict(records[0])
    calls = {}

    def parser(source_path):
        calls["parsed"] = source_path
        return "可用于评估的简历正文"

    def persister(source_path, **kwargs):
        calls["persisted"] = (source_path, kwargs)
        return SimpleNamespace(
            candidate={**active, "name": "新名称", "resume_path": "resumes/g1.pdf"},
            cleanup=SimpleNamespace(failure_count=0),
        )

    temp_dir, _backend, controller = _controller(records)
    try:
        outcome = controller.import_resume(
            active,
            "candidate.pdf",
            parser=parser,
            persister=persister,
            imported_at="2026-08-10 12:00:00",
        )
    finally:
        temp_dir.cleanup()

    assert outcome.resume_text == "可用于评估的简历正文"
    assert calls["parsed"] == "candidate.pdf"
    assert calls["persisted"][1]["identity"] == CandidateController.identity(active)
    assert calls["persisted"][1]["imported_at"] == "2026-08-10 12:00:00"
    assert active["name"] == "新名称"


def test_resume_evaluation_persists_only_second_evaluation_fields():
    records = [{"geek_id": "g1", "job_name": "Java", "match_score": 60}]
    evaluated = {
        **records[0],
        "match_score": 78,
        "resume_eval_adjustment": 18,
        "resume_eval_reason": "项目经验匹配",
        "unrelated_transient": "do-not-save",
    }
    temp_dir, _backend, controller = _controller(records)
    try:
        updated = controller.persist_resume_evaluation(evaluated)
    finally:
        temp_dir.cleanup()

    assert updated is True
    assert records[0]["match_score"] == 78
    assert records[0]["resume_eval_adjustment"] == 18
    assert "unrelated_transient" not in records[0]


def test_resume_revert_restores_pre_resume_score_and_clears_resume_state():
    records = [{
        "geek_id": "g1",
        "job_name": "Java",
        "rule_score": 65,
        "llm_adjustment": 5,
        "match_score": 82,
        "recommend_level": "强烈推荐",
        "resume_eval_adjustment": 12,
        "resume_path": "resumes/g1.pdf",
        "score_breakdown": {"resume_adjustment": 12, "total": 82},
    }]
    active = dict(records[0])
    temp_dir, _backend, controller = _controller(records)
    try:
        outcome = controller.revert_resume_evaluation(
            active,
            resolve_rule_score=lambda candidate: candidate["rule_score"],
            recalc_recommend_level=lambda score: "推荐" if score >= 65 else "待定",
            resume_state_fields=("resume_eval_adjustment", "resume_path"),
        )
    finally:
        temp_dir.cleanup()

    assert outcome.updated is True
    assert outcome.score == 70
    assert records[0]["match_score"] == 70
    assert records[0]["recommend_level"] == "推荐"
    assert records[0]["score_breakdown"] == {"total": 70}
    assert "resume_path" not in active
