import ast
import tempfile
import uuid
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import external_import_service
from external_import_service import (
    BATCH_STATUS_FAILED,
    BATCH_STATUS_IMPORTED,
    BATCH_STATUS_REJECTED,
    BATCH_STATUS_SKIPPED_DUPLICATE,
    REASSIGN_CLEARED_FIELDS,
    ExternalImportDuplicateError,
    ExternalImportPersistenceError,
    find_external_duplicate,
    guess_name_from_filename,
    import_external_candidate,
    import_external_candidates,
    reassign_external_candidate_job,
    update_external_candidate_profile,
)
from gui_external_edit_dialog import ExternalEditDialogWidgets
from gui_external_import_dialog import ExternalImportDialogWidgets
from storage import load_candidates_all


ROOT = Path(__file__).resolve().parents[2]

RESUME_TEXT = """张三
性别：男
年龄：28岁
5年Python开发经验
本科 清华大学 计算机系 2015-2019
期望薪资：20-25K
意向城市：上海
熟悉 Django、Flask、MySQL
"""


def _rule(**overrides):
    rule = {
        "job_uuid": str(uuid.uuid4()),
        "edu": "本科",
        "min_exp": 3,
        "keywords": ["Python", "Django"],
    }
    rule.update(overrides)
    return rule


def _run_import(tmp_dir, text=RESUME_TEXT, rule=None, **overrides):
    base = Path(tmp_dir)
    resume_path = base / "张三-简历.txt"
    resume_path.write_text(text, encoding="utf-8")
    options = {
        "name": "张三",
        "job_name": "Python 工程师",
        "rule": rule if rule is not None else _rule(),
        "source_channel": "猎聘",
        "candidates_path": base / "candidates_all.json",
        "base_dir": base,
        "now": datetime(2026, 8, 10, 12, 0, 0),
    }
    options.update(overrides)
    return import_external_candidate(resume_path, **options)


def test_passed_import_persists_scored_record_with_managed_resume():
    with tempfile.TemporaryDirectory() as tmp_dir:
        rule = _rule()
        result = _run_import(tmp_dir, rule=rule)

        assert result.passed is True
        assert result.score >= 55
        assert result.rejection_reason == ""

        record = result.candidate
        assert record["geek_id"].startswith("ext-")
        assert record["source"] == "external"
        assert record["source_channel"] == "猎聘"
        assert record["job_uuid"] == rule["job_uuid"]
        assert record["job_name"] == "Python 工程师"
        assert record["match_score"] == result.score
        assert record["qualification_status"] == "qualified"
        assert record["followup_status"] == "未沟通"
        assert record["greet_sent"] is False
        assert record["schema_version"] == 2
        assert record["first_seen_at"] == "20260810_120000"
        assert record["batch_timestamp"] == "20260810_120000"
        assert record["last_evaluated_at"] == "20260810_120000"
        assert record["resume_imported_at"] == "2026-08-10 12:00:00"

        persisted = load_candidates_all(str(Path(tmp_dir) / "candidates_all.json"))
        assert len(persisted) == 1
        assert persisted[0]["geek_id"] == record["geek_id"]
        assert (Path(tmp_dir) / persisted[0]["resume_file"]).exists()


def test_rejected_import_is_retained_as_resume_history():
    with tempfile.TemporaryDirectory() as tmp_dir:
        result = _run_import(tmp_dir, rule=_rule(edu="博士"))

        assert result.passed is False
        assert "学历不足" in result.rejection_reason
        # 硬条件淘汰仍重算参考分：剔除硬条件后张三的技能高度匹配
        assert result.reference_score > 0

        persisted = load_candidates_all(str(Path(tmp_dir) / "candidates_all.json"))
        assert len(persisted) == 1
        record = persisted[0]
        assert record["qualification_status"] == "rejected"
        # match_score 按存储约定固定为 0，参考分由 rule_score 承载
        assert record["match_score"] == 0
        assert record["rule_score"] == result.reference_score
        assert record["score_breakdown"]["total"] == result.reference_score
        assert record["recommend_level"] == "未通过"
        assert record["qualification_reasons"]
        assert (Path(tmp_dir) / record["resume_file"]).exists()


def test_low_score_import_is_retained_by_resume_history():
    with tempfile.TemporaryDirectory() as tmp_dir:
        rule = _rule(
            edu="不限",
            min_exp=0,
            keywords=[{"name": "Go语言", "weight": 1}],
        )
        result = _run_import(tmp_dir, rule=rule)

        assert result.passed is True
        assert result.score < 55

        persisted = load_candidates_all(str(Path(tmp_dir) / "candidates_all.json"))
        assert len(persisted) == 1
        assert persisted[0]["match_score"] == result.score
        assert persisted[0]["recommend_level"] == "未通过"


RESUME_WITHOUT_SALARY = """张三
性别：男
年龄：28岁
5年Python开发经验
本科 清华大学 计算机系 2015-2019
意向城市：上海
熟悉 Django、Flask、MySQL
"""


def test_education_year_range_is_not_misread_as_salary():
    """导入简历是自由文本：教育年份段不得被薪资正则误判为期望薪资。"""
    with tempfile.TemporaryDirectory() as tmp_dir:
        rule = _rule(salary_min=10, salary_max=20, work_location="上海")
        result = _run_import(
            tmp_dir,
            text=RESUME_WITHOUT_SALARY,
            rule=rule,
            summary_info_extractor=lambda _text: {"city": "上海"},
        )

        assert "薪资" not in result.rejection_reason
        assert result.passed is True


def test_import_record_carries_school_and_company_from_extractor():
    """画像提取到的毕业学校/最近公司写入记录，供结果表和详情页展示。"""
    with tempfile.TemporaryDirectory() as tmp_dir:
        result = _run_import(
            tmp_dir,
            summary_info_extractor=lambda _text: {
                "school": "江南大学",
                "company": "亚信科技",
            },
        )

        record = result.candidate
        assert record["school"] == "江南大学"
        assert record["company"] == "亚信科技"
        persisted = load_candidates_all(str(Path(tmp_dir) / "candidates_all.json"))
        assert persisted[0]["school"] == "江南大学"
        assert persisted[0]["company"] == "亚信科技"


def test_persistence_failure_recycles_resume_copy():
    with tempfile.TemporaryDirectory() as tmp_dir:
        base = Path(tmp_dir)
        with patch.object(
            external_import_service,
            "mutate_candidates_with_resume_cleanup",
            side_effect=OSError("disk full"),
        ):
            try:
                _run_import(tmp_dir)
                raise AssertionError("导入应当失败")
            except ExternalImportPersistenceError as exc:
                assert exc.copy_retained is False

        txt_files = {path.name for path in base.rglob("*.txt")}
        assert txt_files == {"张三-简历.txt"}


def test_persistence_failure_reports_retained_copy_when_cleanup_fails():
    with tempfile.TemporaryDirectory() as tmp_dir:
        with patch.object(
            external_import_service,
            "mutate_candidates_with_resume_cleanup",
            side_effect=OSError("disk full"),
        ), patch.object(
            external_import_service,
            "delete_managed_resume",
            side_effect=OSError("locked"),
        ):
            try:
                _run_import(tmp_dir)
                raise AssertionError("导入应当失败")
            except ExternalImportPersistenceError as exc:
                assert exc.copy_retained is True


def test_atomic_duplicate_guard_rechecks_latest_snapshot():
    with tempfile.TemporaryDirectory() as tmp_dir:
        rule = _rule()
        first = _run_import(tmp_dir, rule=rule)
        try:
            _run_import(tmp_dir, rule=rule)
            raise AssertionError("同岗位同名记录应在保存事务内被阻止")
        except ExternalImportDuplicateError as exc:
            assert exc.copy_retained is False
        persisted = load_candidates_all(str(Path(tmp_dir) / "candidates_all.json"))
        assert [item["geek_id"] for item in persisted] == [
            first.candidate["geek_id"]
        ]
        assert len(list((Path(tmp_dir) / "resumes").glob("*.txt"))) == 1


def test_find_external_duplicate_matches_same_name_and_job_only():
    job_uuid = str(uuid.uuid4())
    other_job_uuid = str(uuid.uuid4())
    existing = [
        {
            "name": "张三",
            "job_uuid": job_uuid,
            "source": "external",
            "geek_id": "ext-1",
        },
        {"name": "张三", "job_uuid": job_uuid, "geek_id": "boss-1"},
        {
            "name": "张三",
            "job_uuid": other_job_uuid,
            "source": "external",
            "geek_id": "ext-2",
        },
    ]

    found = find_external_duplicate(existing, name="张三", job_uuid=job_uuid)
    assert found is not None
    assert found["geek_id"] == "ext-1"
    assert find_external_duplicate(existing, name="李四", job_uuid=job_uuid) is None
    assert find_external_duplicate(existing, name="张三", job_uuid="") is None


def test_guess_name_from_filename_strips_common_suffixes():
    assert guess_name_from_filename("张三-简历.pdf") == "张三"
    assert guess_name_from_filename("李四_猎聘.pdf") == "李四"
    assert guess_name_from_filename("赵六-高级Java-简历.pdf") == "赵六"
    assert guess_name_from_filename("王五_resume_2026.pdf") == "王五"
    assert guess_name_from_filename("resume.pdf") == ""
    # 噪声词在开头或中间时同样要跳过（招聘网站导出的常见命名）
    assert guess_name_from_filename("简历-胡家敏（系统运维）.doc") == "胡家敏"
    assert guess_name_from_filename("Java求职-刘坤-简历.doc") == "刘坤"
    # 岗位词先行时不许把岗位词当姓名（实测文件名）
    assert guess_name_from_filename("Java研发工程师朱建杨简历_6年.pdf") == "朱建杨"
    assert guess_name_from_filename("高级Java工程师-张伟-简历.docx") == "张伟"


def test_guess_name_from_filename_prefers_segment_before_date():
    """渠道文件名带结尾日期段时，姓名紧邻日期之前（实测文件名）。"""
    # 客户公司“步步高”在姓名段之前，不能取首个中文段
    assert guess_name_from_filename("DIG-667_步步高_Siebel_谢小为_20170307.doc") == "谢小为"
    # 日期段与姓名之间隔着 ASCII 段时继续向前找
    assert guess_name_from_filename("CG_CV_Frey_Wang_王枫_CN_20151013.doc") == "王枫"
    # 11 位手机号不是日期段，仍取首个中文段
    assert guess_name_from_filename("孙伟斌-前端工程师-18752001038.doc") == "孙伟斌"
    # 日期在最前面时回退首个中文段
    assert guess_name_from_filename("20170307_谢小为_简历.doc") == "谢小为"


def test_guess_name_from_filename_skips_company_after_embedded_date():
    """编号段内嵌日期时姓名在锚点之后，客户公司段让位（实测文件名）。"""
    # “RYXQ20170518-0069”编号内嵌日期，公司“上海新致”挡在姓名之前
    assert (
        guess_name_from_filename(
            "RYXQ20170518-0069_上海新致_G6_姚漫_java开发 (1).docx"
        )
        == "姚漫"
    )
    # 日期段紧邻公司、姓名再往后时同样跳过公司段
    assert guess_name_from_filename("20170518_上海新致_王五.docx") == "王五"
    # 姓名紧邻日期之前时优先向前，公司过滤不影响既有方向
    assert guess_name_from_filename("上海新致_姚漫_20170518.docx") == "姚漫"


def test_guess_name_from_filename_fallback_skips_company_segments():
    """无日期锚点时也逐段跳过疑似公司段，不能裸取首个中文段（实测文件名）。"""
    # “城市+岗位-姓名”命名：噪声清理后剩“南京Java-蒋彪”，混合段“南京Java”
    # 不是纯中文姓名段，裸正则会误取“南京”
    assert guess_name_from_filename("南京Java架构师-蒋彪.docx") == "蒋彪"
    # 城市段独立在前时同样跳过
    assert guess_name_from_filename("北京_张三.docx") == "张三"


RESUME_LOW_TEXT = """李四
性别：女
年龄：23岁
1年客服工作经验
高中 某中学 2019-2022
期望薪资：5-8K
意向城市：北京
熟悉 Go语言、Rust
"""


def _write_resume(base: Path, filename: str, text: str) -> Path:
    path = base / filename
    path.write_text(text, encoding="utf-8")
    return path


def _run_batch(paths, tmp_dir, rule=None, **overrides):
    base = Path(tmp_dir)
    options = {
        "job_name": "Python 工程师",
        "rule": rule if rule is not None else _rule(),
        "source_channel": "猎头",
        "candidates_path": base / "candidates_all.json",
        "base_dir": base,
    }
    options.update(overrides)
    return import_external_candidates(paths, **options)


def test_batch_import_isolates_failures_and_skips_duplicates():
    """混合批次：通过/同批重名跳过/淘汰/无法解析四种结果各就各位。

    并行解析下重名两份谁先入库不确定，断言按状态集合而非位置。
    """
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        bad = base / "王五.zip"
        bad.write_bytes(b"not a resume")
        paths = [
            _write_resume(base, "张三-简历A.txt", RESUME_TEXT),
            _write_resume(base, "张三-简历B.txt", RESUME_TEXT),
            _write_resume(base, "李四.txt", RESUME_LOW_TEXT),
            bad,
        ]
        events = []
        summary = _run_batch(
            paths,
            tmp,
            progress_callback=lambda done, total, item: events.append((done, total)),
        )
        statuses = [item.status for item in summary.items]
        assert statuses.count(BATCH_STATUS_IMPORTED) == 1
        assert statuses.count(BATCH_STATUS_SKIPPED_DUPLICATE) == 1
        assert statuses.count(BATCH_STATUS_REJECTED) == 1
        assert statuses.count(BATCH_STATUS_FAILED) == 1
        assert not summary.stopped
        assert events == [(1, 4), (2, 4), (3, 4), (4, 4)]
        # 汇总项按输入文件顺序排列（并行完成顺序不影响展示）
        assert [item.path for item in summary.items] == [str(p) for p in paths]

        imported = next(i for i in summary.items if i.status == BATCH_STATUS_IMPORTED)
        duplicate = next(
            i for i in summary.items if i.status == BATCH_STATUS_SKIPPED_DUPLICATE
        )
        rejected = next(i for i in summary.items if i.status == BATCH_STATUS_REJECTED)
        failed = next(i for i in summary.items if i.status == BATCH_STATUS_FAILED)
        assert imported.candidate["source"] == "external"
        assert imported.resume_text  # 通过者携带全文供 AI 评估
        assert duplicate.candidate is None
        assert "同名" in duplicate.reason
        # 淘汰项携带参考分（剔除硬条件后的技能/经验匹配度）
        assert rejected.score > 0 and rejected.resume_text == ""
        assert failed.candidate is None and failed.reason

        persisted = load_candidates_all(str(base / "candidates_all.json"))
        assert len(persisted) == 2  # 通过 1 条 + 淘汰保留 1 条
        assert sum(c["name"] == "张三" for c in persisted) == 1  # 重名未重复入库


def test_batch_import_parses_files_in_parallel():
    """默认三路并行解析：并发峰值 ≥2，整批耗时显著小于串行。"""
    import threading
    import time

    active = {"cur": 0, "peak": 0}
    lock = threading.Lock()

    def slow_parser(path):
        with lock:
            active["cur"] += 1
            active["peak"] = max(active["peak"], active["cur"])
        try:
            time.sleep(0.3)
            return Path(path).read_text(encoding="utf-8")
        finally:
            with lock:
                active["cur"] -= 1

    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        paths = [
            _write_resume(base, f"{name}-简历.txt", RESUME_TEXT)
            for name in ("张三", "李四", "王五", "赵六")
        ]
        started = time.monotonic()
        summary = _run_batch(paths, tmp, parser=slow_parser)
        elapsed = time.monotonic() - started

    assert not summary.stopped
    assert len(summary.items) == 4
    assert active["peak"] >= 2
    assert elapsed < 0.3 * 4  # 串行至少 1.2s，并行必须显著更快


def test_batch_import_serial_mode_processes_in_input_order():
    """parse_workers=1 退化为串行，逐份按输入顺序处理。"""
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        paths = [
            _write_resume(base, f"{name}-简历.txt", RESUME_TEXT)
            for name in ("张三", "李四", "王五")
        ]
        seen = []
        summary = _run_batch(
            paths,
            tmp,
            parse_workers=1,
            progress_callback=lambda done, total, item: seen.append(item.path),
        )
        assert [item.status for item in summary.items] == [BATCH_STATUS_IMPORTED] * 3
        assert seen == [str(p) for p in paths]


def test_batch_import_stop_event_halts_remaining_files():
    """进度回调置位停止事件后，剩余文件不再处理，已完成导入保留。"""
    import threading

    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        paths = [
            _write_resume(base, "张三-简历.txt", RESUME_TEXT),
            _write_resume(base, "李四-简历.txt", RESUME_TEXT),
            _write_resume(base, "王五-简历.txt", RESUME_TEXT),
        ]
        stop = threading.Event()
        summary = _run_batch(
            paths,
            tmp,
            progress_callback=lambda done, total, item: stop.set(),
            stop_event=stop,
        )
        assert summary.stopped
        assert len(summary.items) == 1
        assert summary.items[0].status == BATCH_STATUS_IMPORTED
        persisted = load_candidates_all(str(base / "candidates_all.json"))
        assert len(persisted) == 1


def test_batch_import_falls_back_to_file_stem_when_name_unknown():
    """文件名猜不出姓名时回退文件名并标注待核对，不阻断导入。"""
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        path = _write_resume(base, "resume_2026.txt", RESUME_TEXT)
        summary = _run_batch([path], tmp)
        item = summary.items[0]
        assert item.status == BATCH_STATUS_IMPORTED
        assert item.name == "resume_2026"
        assert item.name_needs_review
        persisted = load_candidates_all(str(base / "candidates_all.json"))
        assert persisted[0]["name"] == "resume_2026"


def test_batch_import_skips_preexisting_duplicate():
    """与导入前已存在的同岗位同名外部记录撞名时跳过。"""
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        rule = _rule()
        first = _run_import(tmp, rule=rule)
        assert first.passed
        path = _write_resume(base, "张三-简历B.txt", RESUME_TEXT)
        summary = _run_batch([path], tmp, rule=rule)
        assert summary.items[0].status == BATCH_STATUS_SKIPPED_DUPLICATE
        persisted = load_candidates_all(str(base / "candidates_all.json"))
        assert len(persisted) == 1


def _reassign_candidate(**overrides):
    candidate = {
        "geek_id": "ext-abc123def456",
        "source": "external",
        "job_uuid": str(uuid.uuid4()),
        "job_name": "Python 工程师",
        "summary": RESUME_TEXT,
        "match_score": 88,
        "rule_score": 70,
        "resume_eval_adjustment": 18,
        "feedback_status": "合适",
    }
    candidate.update(overrides)
    return candidate


def test_reassign_recomputes_score_and_rebases_rule_score():
    """调岗产出新岗位身份与重算评分，且不就地修改候选人记录。"""
    candidate = _reassign_candidate()
    new_rule = _rule(
        edu="不限",
        min_exp=0,
        keywords=[{"name": "Go语言", "weight": 1}],
    )
    outcome = reassign_external_candidate_job(
        candidate,
        new_job_name="Go 工程师",
        new_rule=new_rule,
        now=datetime(2026, 8, 12, 9, 30, 0),
    )

    assert outcome.passed is True
    assert outcome.score != candidate["match_score"]  # 已按新规则重算
    assert outcome.updates["job_uuid"] == new_rule["job_uuid"]
    assert outcome.updates["job_name"] == "Go 工程师"
    assert outcome.updates["match_rule"] == "Go 工程师"
    assert outcome.updates["match_score"] == outcome.score
    assert outcome.updates["rule_score"] == outcome.score  # 撤回机制的新基准
    assert outcome.updates["last_evaluated_at"] == "20260812_093000"
    assert outcome.cleared_fields == REASSIGN_CLEARED_FIELDS
    # 候选人身份、简历引用和用户业务历史不在更新/清除清单中
    for field in ("geek_id", "resume_file", "feedback_status", "followup_status"):
        assert field not in outcome.updates
        assert field not in outcome.cleared_fields
    # 服务只计算更新，持久化与就地生效由调用方负责
    assert candidate["job_name"] == "Python 工程师"
    assert candidate["match_score"] == 88


def test_reassign_to_stricter_job_rejects_with_reason():
    """新岗位硬条件不满足时产出淘汰更新，供记录进入淘汰视图。"""
    candidate = _reassign_candidate()
    outcome = reassign_external_candidate_job(
        candidate,
        new_job_name="架构师",
        new_rule=_rule(edu="博士"),
    )

    assert outcome.passed is False
    assert outcome.score == 0
    assert "学历不足" in outcome.rejection_reason
    assert outcome.reference_score > 0  # 剔除硬条件后的参考匹配分
    assert outcome.updates["qualification_status"] == "rejected"
    assert outcome.updates["match_score"] == 0
    assert outcome.updates["rule_score"] == outcome.reference_score


def test_reassign_guards_non_external_same_job_missing_uuid_and_short_text():
    candidate = _reassign_candidate()

    try:
        reassign_external_candidate_job(
            {**candidate, "source": "boss"},
            new_job_name="Go 工程师",
            new_rule=_rule(),
        )
        raise AssertionError("非外部候选人应当被拒绝")
    except ValueError as exc:
        assert "只有外部渠道" in str(exc)

    try:
        reassign_external_candidate_job(
            candidate,
            new_job_name="Go 工程师",
            new_rule=_rule(job_uuid=candidate["job_uuid"]),
        )
        raise AssertionError("调整到当前岗位应当被拒绝")
    except ValueError as exc:
        assert "已归属该岗位" in str(exc)

    try:
        reassign_external_candidate_job(
            candidate,
            new_job_name="Go 工程师",
            new_rule=_rule(job_uuid=""),
        )
        raise AssertionError("缺少稳定 ID 的岗位应当被拒绝")
    except ValueError as exc:
        assert "缺少稳定 ID" in str(exc)

    try:
        reassign_external_candidate_job(
            {**candidate, "summary": "内容太少"},
            new_job_name="Go 工程师",
            new_rule=_rule(),
        )
        raise AssertionError("缺少简历全文应当被拒绝")
    except ValueError as exc:
        assert "缺少简历全文" in str(exc)


def test_external_import_dialog_exposes_widgets_and_stays_tk_only():
    assert set(ExternalImportDialogWidgets.__dataclass_fields__) == {
        "window",
        "file_var",
        "name_var",
        "job_var",
        "channel_var",
        "note_text",
        "feedback_var",
        "confirm_button",
        "cancel_button",
        "progress_var",
        "progress_bar",
        "summary_var",
        "summary_label",
        "summary_detail_var",
        "summary_tree",
        "eval_var",
        "ai_enhance_var",
        "ai_resume_eval_var",
        "close_dialog",
    }
    source = (ROOT / "gui_external_import_dialog.py").read_text(encoding="utf-8")
    assert "on_confirm(" in source
    assert "run_batch(" in source
    assert "parse_resume_text" not in source
    assert "import_external_candidate" not in source
    assert "CANDIDATES_PATH" not in source


def test_external_import_service_excludes_gui_bossmaster_and_network_dependencies():
    tree = ast.parse(
        (ROOT / "external_import_service.py").read_text(encoding="utf-8")
    )
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)

    forbidden = {
        "bossmaster",
        "gui_main",
        "tkinter",
        "requests",
        "socket",
        "subprocess",
        "urllib",
    }
    assert not (imported & forbidden)


RESUME_NO_AGE_TEXT = """李四
性别：男
5年Python开发经验
本科 南京大学 计算机系 2015-2019
期望薪资：12-15K
意向城市：上海
熟悉 Django、Flask、MySQL
"""

RESUME_BARE_TEXT = """王五
性别：女
4年Python开发经验
本科 东南大学 2016-2020
意向城市：上海
熟悉 Django、Flask、MySQL
"""


def _fake_enhancer(result, calls):
    """构造记录调用的假 enhancer；result 为 Exception 实例时抛出。"""
    def enhancer(resume_text, regex_info):
        calls.append((resume_text, regex_info))
        if isinstance(result, Exception):
            raise result
        return result

    return enhancer


def test_import_profile_enhancer_filled_age_triggers_rejection_with_audit():
    """AI 补全的年龄参与硬条件筛选；淘汰理由保留审计说明。"""
    with tempfile.TemporaryDirectory() as tmp:
        calls = []

        def enhancer(resume_text, regex_info):
            calls.append(1)
            return {
                "info": {**regex_info, "age": "45"},
                "filled": [{"field": "age", "label": "年龄", "value": "45"}],
                "conflicts": [],
                "error": "",
            }

        result = _run_import(
            tmp,
            text=RESUME_NO_AGE_TEXT,
            rule=_rule(max_age=40),
            profile_enhancer=enhancer,
        )
        assert calls == [1]
        assert result.passed is False
        assert "年龄不符" in result.rejection_reason
        record = result.candidate
        assert record["age"] == "45"
        assert record["profile_ai_filled"] == [
            {"field": "age", "label": "年龄", "value": "45"}
        ]
        assert any(
            "AI 补全的画像字段（年龄）" in reason
            for reason in record["qualification_reasons"]
        )
        # 淘汰结论不因审计说明改变
        assert record["qualification_status"] == "rejected"


def test_import_profile_enhancer_filled_salary_passes_and_pins_record():
    """AI 补空的薪资写入记录并参与薪资硬条件检查。"""
    with tempfile.TemporaryDirectory() as tmp:
        def enhancer(resume_text, regex_info):
            return {
                "info": {**regex_info, "salary": "10K"},
                "filled": [{"field": "salary", "label": "薪资", "value": "10K"}],
                "conflicts": [],
                "error": "",
            }

        result = _run_import(
            tmp,
            text=RESUME_BARE_TEXT,
            rule=_rule(salary_min=5, salary_max=20),
            profile_enhancer=enhancer,
        )
        assert result.passed is True
        assert result.candidate["salary"] == "10K"
        assert result.candidate["profile_ai_filled"][0]["field"] == "salary"


def test_import_profile_enhancer_conflict_keeps_regex_and_flags_review():
    """冲突保留规则值，通过筛选的记录转人工复核。"""
    with tempfile.TemporaryDirectory() as tmp:
        def enhancer(resume_text, regex_info):
            return {
                "info": {**regex_info, "age": "28"},
                "filled": [],
                "conflicts": [
                    {"field": "age", "label": "年龄", "rule": "28", "ai": "45"}
                ],
                "error": "",
            }

        result = _run_import(tmp, profile_enhancer=enhancer)
        assert result.passed is True
        record = result.candidate
        assert record["age"] == "28"
        assert record["qualification_status"] == "manual_review"
        assert any(
            "AI 画像与规则识别不一致：年龄（规则 28 / AI 45），已保留规则值" in reason
            for reason in record["qualification_reasons"]
        )
        assert record["profile_conflicts"] == [
            {"field": "age", "label": "年龄", "rule": "28", "ai": "45"}
        ]


def test_import_profile_enhancer_exception_falls_back_to_regex():
    """enhancer 抛异常按纯正则导入，错误落盘不阻断。"""
    with tempfile.TemporaryDirectory() as tmp:
        calls = []
        result = _run_import(
            tmp,
            profile_enhancer=_fake_enhancer(RuntimeError("boom"), calls),
        )
        assert calls  # 确实尝试过增强
        assert result.passed is True
        record = result.candidate
        assert record["profile_ai_error"] == "boom"
        assert "profile_ai_filled" not in record
        assert "profile_conflicts" not in record


def test_import_profile_enhancer_bad_shape_falls_back_to_regex():
    """enhancer 返回非法结构按纯正则导入并记固定错误文案。"""
    with tempfile.TemporaryDirectory() as tmp:
        calls = []
        result = _run_import(
            tmp,
            profile_enhancer=_fake_enhancer({"info": "not-a-dict"}, calls),
        )
        assert result.passed is True
        assert result.candidate["profile_ai_error"] == "AI 增强返回格式异常，已按规则识别结果导入"


def test_import_profile_enhancer_none_silently_skips():
    """enhancer 返回 None 是静默跳过：不增强也不留任何痕迹。"""
    with tempfile.TemporaryDirectory() as tmp:
        calls = []
        result = _run_import(tmp, profile_enhancer=_fake_enhancer(None, calls))
        assert calls
        assert result.passed is True
        for key in ("profile_ai_filled", "profile_conflicts", "profile_ai_error"):
            assert key not in result.candidate


def test_batch_import_enhancer_passthrough_and_duplicate_skip_saves_quota():
    """批量透传 enhancer；同名重复在导入前跳过，不消耗 AI 调用。"""
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        rule = _rule()
        first = _run_import(tmp, rule=rule)  # 预置张三
        assert first.passed
        paths = [
            _write_resume(base, "张三-简历B.txt", RESUME_TEXT),
            _write_resume(base, "王五.txt", RESUME_BARE_TEXT),
        ]
        calls = []
        summary = _run_batch(
            paths,
            tmp,
            rule=rule,
            profile_enhancer=_fake_enhancer(None, calls),
        )
        statuses = [item.status for item in summary.items]
        assert statuses == [BATCH_STATUS_SKIPPED_DUPLICATE, BATCH_STATUS_IMPORTED]
        assert len(calls) == 1  # 重复跳过者未触发 AI


def test_batch_import_enhancer_failure_does_not_block_others():
    """整批 enhancer 异常时各份仍按纯正则导入，错误逐份落盘。"""
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        paths = [
            _write_resume(base, "张三.txt", RESUME_TEXT),
            _write_resume(base, "王五.txt", RESUME_BARE_TEXT),
        ]
        calls = []
        summary = _run_batch(
            paths,
            tmp,
            profile_enhancer=_fake_enhancer(RuntimeError("provider down"), calls),
        )
        assert len(calls) == 2
        assert [item.status for item in summary.items] == [
            BATCH_STATUS_IMPORTED,
            BATCH_STATUS_IMPORTED,
        ]
        for item in summary.items:
            assert item.candidate["profile_ai_error"] == "provider down"


# ---------- 编辑候选人信息（update_external_candidate_profile） ----------

EDIT_JOB_UUID = "11111111-1111-1111-1111-111111111111"
EDIT_JOB_UUID_2 = "22222222-2222-2222-2222-222222222222"

EDIT_RESUME_TEXT = """鲍佳佳
性别：女
本科 南京林业大学 2012-2016
5年 Java 开发经验
期望薪资：15-20K
期望城市：南京
"""


def _edit_candidate(**overrides):
    candidate = {
        "geek_id": "ext-edit1",
        "source": "external",
        "name": "鲍佳佳",
        "job_uuid": EDIT_JOB_UUID,
        "job_name": "Java 岗",
        "summary": EDIT_RESUME_TEXT,
        "gender": "女",
        "age": "",
        "education": "本科",
        "exp_years": "",
        "salary": "15-20K",
        "city": "南京",
        "job_status": "",
        "school": "南京林业大学",
        "company": "",
        "profile_ai_filled": [
            {"field": "company", "label": "最近公司", "value": "华泰证券"}
        ],
        "qualification_status": "qualified",
        "match_score": 70,
    }
    candidate.update(overrides)
    return candidate


def _edit_rule(**overrides):
    rule = {
        "job_uuid": EDIT_JOB_UUID,
        "min_exp": 0,
        "edu": "不限",
        "max_age": None,
        "work_location": "",
        "required_conditions": [],
        "tech_conditions": [],
    }
    rule.update(overrides)
    return rule


def _edit_fields(candidate, **changes):
    """模拟对话框全量回传：未修改的字段带回当前记录值。"""
    fields = {
        key: str(candidate.get(key) or "")
        for key in (
            "gender", "age", "education", "exp_years", "salary",
            "city", "job_status", "school", "company",
        )
    }
    fields.update(changes)
    return fields


def test_profile_update_display_only_edit_skips_refilter_and_clears_ai_trace():
    """只改学校/公司等展示字段：不重筛，且被改字段的 AI 补全痕迹随之移除。"""
    candidate = _edit_candidate()
    result = update_external_candidate_profile(
        candidate,
        name="鲍佳佳",
        fields=_edit_fields(candidate, company="朗新科技"),
        candidates=[candidate],
        rule=_edit_rule(),
    )
    assert result.refiltered is False
    assert result.passed is True
    assert result.updates["company"] == "朗新科技"
    assert result.updates["profile_ai_filled"] == []
    assert "match_score" not in result.updates
    assert result.cleared_fields == ()


def test_profile_update_without_any_change_is_rejected():
    candidate = _edit_candidate()
    try:
        update_external_candidate_profile(
            candidate,
            name="鲍佳佳",
            fields=_edit_fields(candidate),
            candidates=[candidate],
            rule=_edit_rule(),
        )
    except ValueError as exc:
        assert "没有需要保存的修改" in str(exc)
    else:
        raise AssertionError("无修改应被拒绝")


def test_profile_update_rejects_invalid_values_and_boss_candidates():
    candidate = _edit_candidate()
    for bad in ({"salary": "两万"}, {"age": "abc"}, {"education": "大学"}):
        try:
            update_external_candidate_profile(
                candidate,
                name="鲍佳佳",
                fields=_edit_fields(candidate, **bad),
                candidates=[candidate],
                rule=_edit_rule(),
            )
        except ValueError:
            pass
        else:
            raise AssertionError(f"非法值应被拒绝: {bad}")
    boss_candidate = _edit_candidate(source="boss")
    try:
        update_external_candidate_profile(
            boss_candidate,
            name="鲍佳佳",
            fields=_edit_fields(boss_candidate, company="某公司"),
            candidates=[boss_candidate],
            rule=_edit_rule(),
        )
    except ValueError as exc:
        assert "外部渠道" in str(exc)
    else:
        raise AssertionError("BOSS 候选人不应支持该编辑")


def test_profile_update_age_beyond_limit_refilters_to_rejection():
    """年龄修正触发重筛：超过岗位上限时淘汰并保留参考分。"""
    candidate = _edit_candidate()
    result = update_external_candidate_profile(
        candidate,
        name="鲍佳佳",
        fields=_edit_fields(candidate, age="40"),
        candidates=[candidate],
        rule=_edit_rule(max_age=35),
    )
    assert result.refiltered is True
    assert result.passed is False
    assert "年龄" in result.rejection_reason
    assert result.updates["match_score"] == 0
    assert result.updates["rule_score"] == result.reference_score > 0
    assert result.cleared_fields == ()  # 岗位未变，评估字段保留


def test_profile_update_pinned_education_drives_hard_check():
    """手工钉定的学历参与硬条件：本科要求下钉定大专即学历不足。"""
    candidate = _edit_candidate()
    result = update_external_candidate_profile(
        candidate,
        name="鲍佳佳",
        fields=_edit_fields(candidate, education="大专"),
        candidates=[candidate],
        rule=_edit_rule(edu="本科"),
    )
    assert result.refiltered is True
    assert result.passed is False
    assert "学历" in result.rejection_reason


def test_profile_update_blank_field_skips_that_hard_check():
    """清空画像字段表示未识别：对应硬条件跳过而不是按空值淘汰。"""
    candidate = _edit_candidate(age="40")
    result = update_external_candidate_profile(
        candidate,
        name="鲍佳佳",
        fields=_edit_fields(candidate, age=""),
        candidates=[candidate],
        rule=_edit_rule(max_age=35),
    )
    assert result.refiltered is True
    assert result.passed is True
    assert result.updates["age"] == ""


def test_profile_update_blank_field_suppresses_resume_text_fallback():
    """人工清空必须压过简历原文中的旧画像，避免误识别再次生效。"""
    candidate = _edit_candidate(
        age="40",
        gender="男",
        education="大专",
        exp_years="2",
        job_status="暂不考虑",
        summary=(
            EDIT_RESUME_TEXT
            + "\n年龄：40岁\n性别：男\n大专\n2年工作经验\n求职状态：暂不考虑"
        ),
    )
    result = update_external_candidate_profile(
        candidate,
        name="鲍佳佳",
        fields=_edit_fields(
            candidate,
            age="",
            gender="",
            education="",
            exp_years="",
            job_status="",
        ),
        candidates=[candidate],
        rule=_edit_rule(max_age=35, gender="女", edu="本科", min_exp=5),
    )
    assert result.passed is True
    assert result.updates["age"] == ""
    assert result.updates["gender"] == ""
    assert result.updates["education"] == ""
    assert result.updates["exp_years"] == ""
    assert result.updates["job_status"] == ""


def test_profile_update_reapplies_existing_resume_adjustment_to_new_rule_score():
    """同岗位画像重筛保留简历评估时，最终分必须继续包含既有调整分。"""
    candidate = _edit_candidate(
        rule_score=60,
        match_score=70,
        resume_eval_adjustment=10,
        resume_eval_at="2026-08-17 10:00:00",
    )
    result = update_external_candidate_profile(
        candidate,
        name="鲍佳佳",
        fields=_edit_fields(candidate, city="上海"),
        candidates=[candidate],
        rule=_edit_rule(),
    )
    assert result.updates["rule_score"] >= 0
    assert result.updates["match_score"] == min(
        100, result.updates["rule_score"] + 10
    )
    assert result.score == result.updates["match_score"]


def test_profile_update_job_change_clears_old_evaluation_fields():
    """调岗沿用旧语义：清除旧岗位评估与复核字段，job 身份切换。"""
    candidate = _edit_candidate()
    result = update_external_candidate_profile(
        candidate,
        name="鲍佳佳",
        fields=_edit_fields(candidate),
        candidates=[candidate],
        rule=_edit_rule(job_uuid=EDIT_JOB_UUID_2),
        job_name="新 Java 岗",
    )
    assert result.job_changed is True
    assert result.refiltered is True
    assert result.cleared_fields == REASSIGN_CLEARED_FIELDS
    assert result.updates["job_uuid"] == EDIT_JOB_UUID_2
    assert result.updates["job_name"] == "新 Java 岗"


def test_profile_update_name_duplicate_excludes_self():
    """同岗位同名查重排除自身；目标岗位已有同名外部候选人时阻止。"""
    candidate = _edit_candidate()
    other = _edit_candidate(geek_id="ext-other")
    try:
        update_external_candidate_profile(
            candidate,
            name="鲍佳佳",
            fields=_edit_fields(candidate, company="某公司"),
            candidates=[candidate, other],
            rule=_edit_rule(),
        )
    except ValueError as exc:
        assert "同名" in str(exc)
    else:
        raise AssertionError("同岗位同名应被阻止")


def test_external_edit_dialog_exposes_widgets_and_stays_tk_only():
    assert set(ExternalEditDialogWidgets.__dataclass_fields__) == {
        "window",
        "name_var",
        "job_var",
        "gender_var",
        "age_var",
        "education_var",
        "exp_years_var",
        "salary_var",
        "city_var",
        "job_status_var",
        "school_var",
        "company_var",
        "feedback_var",
        "save_button",
        "cancel_button",
    }
    source = (ROOT / "gui_external_edit_dialog.py").read_text(encoding="utf-8")
    assert "on_confirm(" in source
    assert "update_external_candidate_profile" not in source
    assert "filter_candidate" not in source
    assert "CANDIDATES_PATH" not in source
