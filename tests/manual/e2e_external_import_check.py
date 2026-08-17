"""One-off end-to-end check: real job config + temp storage + import service."""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from external_import_service import import_external_candidate, find_external_duplicate
from job_config_store import load_job_config_snapshot
from paths import CONFIG_PATH
from storage import load_candidates_all
from bossmaster import extract_summary_info

config = load_job_config_snapshot(CONFIG_PATH)
rules = config["job_requirements"]
job_name = next(name for name in rules if name != "default")
rule = rules[job_name]
print(f"岗位: {job_name}")
print(f"  job_uuid: {rule.get('job_uuid')}")
print(f"  edu={rule.get('edu')} min_exp={rule.get('min_exp')} keywords={len(rule.get('keywords', []))}个")

resume_text = """李四
性别：男
年龄：30岁
8年Python后端开发经验
本科 浙江大学 计算机科学与技术 2014-2018
期望薪资：12-15K
意向城市：南京
求职状态：在职
熟悉 Python、Django、MySQL、Redis、Docker、Kubernetes、微服务架构
"""
# 用真实岗位的关键词填充简历，保证技能命中
for kw in rule.get("keywords", [])[:8]:
    kw_name = kw["name"] if isinstance(kw, dict) else kw
    resume_text += f"精通 {kw_name}\n"

with tempfile.TemporaryDirectory() as tmp:
    base = Path(tmp)
    resume_path = base / "李四-简历.txt"
    resume_path.write_text(resume_text, encoding="utf-8")

    result = import_external_candidate(
        resume_path,
        name="李四",
        job_name=job_name,
        rule=rule,
        source_channel="猎头",
        source_note="某猎头公司推荐",
        candidates_path=base / "candidates_all.json",
        base_dir=base,
        summary_info_extractor=extract_summary_info,
    )
    print(f"\n导入结果: passed={result.passed} score={result.score} reason={result.rejection_reason!r}")
    record = result.candidate
    print(f"  geek_id={record['geek_id']} level={record['recommend_level']}")
    print(f"  source={record['source']} channel={record['source_channel']} note={record.get('source_note')}")
    print(f"  画像: gender={record['gender']!r} age={record['age']!r} exp={record['exp_years']!r} edu={record['education']!r}")
    print(f"  画像: salary={record['salary']!r} city={record['city']!r} status={record['job_status']!r}")
    print(f"  技能命中: {record['skill_match_ratio']} {record['skill_matches']}")
    print(f"  简历副本: {record['resume_file']} exists={(base / record['resume_file']).exists()}")

    persisted = load_candidates_all(str(base / "candidates_all.json"))
    print(f"\n持久化: {len(persisted)} 条, geek_id 匹配={persisted[0]['geek_id'] == record['geek_id']}")

    dup = find_external_duplicate(persisted, name="李四", job_uuid=rule["job_uuid"])
    print(f"查重: {'命中(预期)' if dup else '未命中(异常!)'}")

    # 二次导入同一姓名 → 应产生新记录（用户确认后）
    result2 = import_external_candidate(
        resume_path,
        name="李四",
        job_name=job_name,
        rule=rule,
        source_channel="猎头",
        candidates_path=base / "candidates_all.json",
        base_dir=base,
        summary_info_extractor=extract_summary_info,
        allow_duplicate=True,
    )
    persisted2 = load_candidates_all(str(base / "candidates_all.json"))
    print(f"重复导入: {len(persisted2)} 条, geek_id 不同={persisted2[0]['geek_id'] != persisted2[1]['geek_id']}")

    # 回归: 简历含教育年份段(2014-2018)但未写期望薪资时, 不得误判为 2014K 薪资拒绝
    no_salary_text = """王五
性别：女
年龄：28岁
6年Python后端开发经验
本科 南京大学 软件工程 2014-2018
意向城市：南京
求职状态：离职
"""
    for kw in rule.get("keywords", [])[:8]:
        kw_name = kw["name"] if isinstance(kw, dict) else kw
        no_salary_text += f"熟悉 {kw_name}\n"
    no_salary_path = base / "王五-简历.txt"
    no_salary_path.write_text(no_salary_text, encoding="utf-8")
    result3 = import_external_candidate(
        no_salary_path,
        name="王五",
        job_name=job_name,
        rule=rule,
        source_channel="内推",
        candidates_path=base / "candidates_all.json",
        base_dir=base,
        summary_info_extractor=extract_summary_info,
    )
    salary_misjudge = "2014K" in result3.rejection_reason or "2014" in result3.rejection_reason
    print(f"\n无薪资简历: passed={result3.passed} score={result3.score} reason={result3.rejection_reason!r}")
    print(f"年份段误判薪资: {'发生(缺陷!)' if salary_misjudge else '未发生(预期)'}")

    # ---- 批量导入：通过 / 同批重名跳过 / 淘汰 / 无法解析 ----
    from external_import_service import import_external_candidates

    low_text = (
        "赵七\n性别：女\n年龄：23岁\n高中学历\n1年客服工作经验\n"
        "期望薪资：5-8K\n意向城市：北京\n求职状态：在职\n"
        "熟悉 Go语言、Rust 基础语法，主要负责客户咨询接待与售后记录整理工作。\n"
    )
    (base / "孙八-简历.txt").write_text(resume_text, encoding="utf-8")
    (base / "孙八-简历副本.txt").write_text(resume_text, encoding="utf-8")
    (base / "赵七.txt").write_text(low_text, encoding="utf-8")
    (base / "broken.zip").write_bytes(b"not a resume")

    events = []
    summary = import_external_candidates(
        [
            base / "孙八-简历.txt",
            base / "孙八-简历副本.txt",
            base / "赵七.txt",
            base / "broken.zip",
        ],
        job_name=job_name,
        rule=rule,
        source_channel="智联招聘",
        candidates_path=base / "candidates_all.json",
        base_dir=base,
        summary_info_extractor=extract_summary_info,
        progress_callback=lambda done, total, item: events.append((done, total, item.name, item.status)),
    )
    print("\n批量导入:")
    for done, total, name, status in events:
        print(f"  {done}/{total} {name}: {status}")
    print(f"  stopped={summary.stopped} counts: imported={summary.count('imported')} "
          f"rejected={summary.count('rejected')} dup={summary.count('skipped_duplicate')} "
          f"failed={summary.count('failed')}")
    persisted4 = load_candidates_all(str(base / "candidates_all.json"))
    print(f"  库内总数: {len(persisted4)}（李四×2 + 王五 + 孙八 + 赵七淘汰 = 5）")

    # ---- 调整归属岗位：重算评分 + 清除旧评估字段 + 控制器持久化 ----
    import uuid as _uuid

    from candidate_controller import CandidateController, CandidatePersistence
    from external_import_service import reassign_external_candidate_job
    from storage import (
        mark_candidate_greeted,
        mark_candidate_not_greeted,
        mutate_candidates_all,
        mutate_candidates_with_resume_cleanup,
        remove_candidates_all_with_resume_cleanup,
        update_candidate_records,
    )

    other_names = [n for n in rules if n != "default" and n != job_name]
    if other_names:
        new_job_name, new_rule = other_names[0], rules[other_names[0]]
    else:
        new_job_name = "临时·高学历岗位"
        new_rule = {**rule, "job_uuid": str(_uuid.uuid4()), "edu": "博士"}

    target = next(c for c in persisted4 if c["name"] == "孙八")
    stale_fields = {
        "resume_eval_adjustment": 9,
        "resume_eval_reason": "旧岗位评估结论",
        "llm_evaluated": True,
        "review_passed_at": "20260810_120000",
        "contact_approved_at": "20260810_120100",
        "feedback_status": "合适",
    }
    update_candidate_records(
        lambda r: r.get("geek_id") == target["geek_id"],
        lambda r: r.update(stale_fields),
        base / "candidates_all.json",
    )
    target.update(stale_fields)

    reassign = reassign_external_candidate_job(
        target,
        new_job_name=new_job_name,
        new_rule=new_rule,
        summary_info_extractor=extract_summary_info,
    )
    controller = CandidateController(
        base / "candidates_all.json",
        base,
        CandidatePersistence(
            update_records=update_candidate_records,
            mutate_all=mutate_candidates_all,
            mutate_with_resume_cleanup=mutate_candidates_with_resume_cleanup,
            remove_with_resume_cleanup=remove_candidates_all_with_resume_cleanup,
            mark_greeted=mark_candidate_greeted,
            mark_not_greeted=mark_candidate_not_greeted,
        ),
    )
    reassign_ok = controller.reassign_job(
        target, reassign.updates, reassign.cleared_fields
    )
    after = next(
        c
        for c in load_candidates_all(str(base / "candidates_all.json"))
        if c["geek_id"] == target["geek_id"]
    )
    print(f"\n调岗: {job_name} → {new_job_name}")
    print(f"  passed={reassign.passed} score={reassign.score} reason={reassign.rejection_reason!r}")
    print(f"  持久化成功={reassign_ok} 新 job_uuid 命中={after['job_uuid'] == new_rule['job_uuid']}")
    print(f"  旧评估字段已清除={'resume_eval_adjustment' not in after and 'review_passed_at' not in after}")
    print(f"  反馈保留={after.get('feedback_status') == '合适'} rule_score={after.get('rule_score')}")
    final_all = load_candidates_all(str(base / "candidates_all.json"))
    print(f"  调岗后库内: {len(final_all)} 条 → "
          f"{[(c.get('name'), c.get('job_name'), c.get('qualification_status')) for c in final_all]}")

assert summary.count("imported") == 1 and summary.count("skipped_duplicate") == 1
assert summary.count("rejected") == 1 and summary.count("failed") == 1
assert len(persisted4) == 5, len(persisted4)
assert reassign_ok
assert after["job_uuid"] == new_rule["job_uuid"]
assert after["job_name"] == new_job_name
assert after["match_score"] == reassign.score
if reassign.passed:
    assert after["rule_score"] == reassign.score
else:
    # 淘汰调岗：match_score 固定为 0，rule_score 承载参考匹配分
    assert after["rule_score"] == reassign.reference_score > 0
assert "resume_eval_adjustment" not in after and "llm_evaluated" not in after
assert "review_passed_at" not in after and "contact_approved_at" not in after
assert after.get("feedback_status") == "合适"  # 用户业务历史保留
assert len(final_all) == 5  # 总数不变
print("\nE2E_OK")
