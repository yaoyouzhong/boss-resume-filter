import tempfile
from pathlib import Path
from types import SimpleNamespace

from candidate_controller import CandidateController, CandidatePersistence


JOB_UUID_A = "11111111-1111-4111-8111-111111111111"
JOB_UUID_B = "22222222-2222-4222-8222-222222222222"


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


def test_same_named_jobs_with_different_uuids_do_not_cross_update():
    records = [
        {
            "geek_id": "g1",
            "job_uuid": JOB_UUID_A,
            "job_name": "Java 工程师",
            "followup_status": "未沟通",
        },
        {
            "geek_id": "g1",
            "job_uuid": JOB_UUID_B,
            "job_name": "Java 工程师",
            "followup_status": "未沟通",
        },
    ]
    active = dict(records[1])
    temp_dir, _backend, controller = _controller(records)
    try:
        updated = controller.update_followup(
            "g1",
            "Java 工程师",
            "已回复",
            "仅更新第二个岗位",
            job_uuid=JOB_UUID_B,
            candidate=active,
        )
    finally:
        temp_dir.cleanup()

    assert updated is True
    assert CandidateController.identity(records[0]) != CandidateController.identity(records[1])
    assert records[0]["followup_status"] == "未沟通"
    assert records[1]["followup_status"] == "已回复"


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


def test_reassign_job_updates_by_old_identity_and_mirrors_active_candidate():
    """调岗先按旧身份定位记录，再应用新 job_uuid 并清除旧岗位语境字段。"""
    records = [
        {
            "geek_id": "ext-abc",
            "job_uuid": JOB_UUID_A,
            "job_name": "Java",
            "match_score": 55,
            "resume_eval_adjustment": 8,
            "review_passed_at": "t0",
            "feedback_status": "合适",
        },
        {
            "geek_id": "ext-xyz",
            "job_uuid": JOB_UUID_B,
            "job_name": "Python",
            "match_score": 61,
        },
    ]
    active = dict(records[0])
    temp_dir, _backend, controller = _controller(records)
    try:
        updated = controller.reassign_job(
            active,
            {
                "job_uuid": JOB_UUID_B,
                "job_name": "Python",
                "match_score": 72,
                "rule_score": 72,
            },
            ("resume_eval_adjustment", "review_passed_at"),
        )
    finally:
        temp_dir.cleanup()

    assert updated is True
    # 持久化记录被旧身份命中并换到新岗位，旧评估/复核字段被清除
    assert records[0]["job_uuid"] == JOB_UUID_B
    assert records[0]["job_name"] == "Python"
    assert records[0]["match_score"] == 72
    assert "resume_eval_adjustment" not in records[0]
    assert "review_passed_at" not in records[0]
    # 用户业务历史保留，其他记录不受影响
    assert records[0]["feedback_status"] == "合适"
    assert records[1]["match_score"] == 61
    # 内存中的候选人镜像同一变更
    assert active["job_uuid"] == JOB_UUID_B
    assert active["match_score"] == 72
    assert "resume_eval_adjustment" not in active


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


def test_resume_revert_keeps_rejected_candidate_score_frozen():
    """已淘汰记录撤回简历评估：只清除评估状态，分数与推荐等级保持冻结。"""
    records = [{
        "geek_id": "g1",
        "job_name": "Java",
        "rule_score": 42,
        "llm_adjustment": 15,
        "match_score": 0,
        "recommend_level": "未通过",
        "qualification_status": "rejected",
        "resume_eval_adjustment": 10,
        "resume_path": "resumes/g1.pdf",
        "score_breakdown": {"base": 25, "skill": 12, "resume_adjustment": 10, "total": 42},
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
    assert outcome.score == 0, "淘汰记录撤回不产生分数变化"
    assert records[0]["match_score"] == 0
    assert records[0]["recommend_level"] == "未通过"
    assert records[0]["rule_score"] == 42
    assert records[0]["score_breakdown"]["total"] == 42
    assert "resume_adjustment" not in records[0]["score_breakdown"]
    assert "resume_path" not in records[0]


def test_resume_revert_with_reduced_fields_keeps_external_resume_reference():
    """外部候选人撤销评估传缩减字段集：清评估字段但保留简历文件引用。

    外部记录的受管简历副本就是档案本体，引用被清会失去原件且无法再评分。
    """
    records = [{
        "geek_id": "ext-1",
        "job_name": "Java",
        "source": "external",
        "rule_score": 65,
        "llm_adjustment": 0,
        "match_score": 77,
        "recommend_level": "强烈推荐",
        "resume_eval_adjustment": 12,
        "resume_eval_at": "20260817_100000",
        "resume_file": "resumes/ext-1.docx",
        "resume_original_name": "鲍佳佳.docx",
        "resume_imported_at": "2026-08-17 10:00:00",
        "score_breakdown": {"resume_adjustment": 12, "total": 77},
    }]
    active = dict(records[0])
    temp_dir, _backend, controller = _controller(records)
    try:
        outcome = controller.revert_resume_evaluation(
            active,
            resolve_rule_score=lambda candidate: candidate["rule_score"],
            recalc_recommend_level=lambda score: "推荐" if score >= 65 else "待定",
            resume_state_fields=("resume_eval_adjustment", "resume_eval_at"),
        )
    finally:
        temp_dir.cleanup()

    assert outcome.updated is True
    assert records[0]["resume_file"] == "resumes/ext-1.docx"
    assert records[0]["resume_original_name"] == "鲍佳佳.docx"
    assert records[0]["resume_imported_at"] == "2026-08-17 10:00:00"
    assert "resume_eval_adjustment" not in records[0]
    assert "resume_eval_at" not in records[0]
