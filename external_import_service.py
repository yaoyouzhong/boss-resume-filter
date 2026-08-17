"""Transactional import for candidates sourced outside BOSS scanning.

Single-resume import flow: parse resume text, run rule filtering, build a
synthetic-identity candidate record, attach the managed resume copy and
append everything in one atomic save. Batch import loops the single-file
transaction serially: each file is checked against a freshly read snapshot
for same-job same-name duplicates, and per-file failures are summarized
without aborting the rest of the batch.
"""
from __future__ import annotations

import re
import uuid
from collections.abc import Callable, Iterable
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from candidate_workflow import EXTERNAL_CANDIDATE_SOURCE, is_external_candidate
from constants import (
    SCORE_THRESHOLD_PASS,
    SCORE_THRESHOLD_RECOMMEND,
    SCORE_THRESHOLD_STRONG,
)
from data_schema import CANDIDATE_SCHEMA_VERSION, normalize_job_uuid
from filtering import _parse_candidate_salary_range, filter_candidate
from resume_import_service import ResumeCopyError
from resume_parser import parse_resume_text
from resume_store import (
    UnmanagedResumePathError,
    delete_managed_resume,
    store_resume_copy,
)
from storage import mutate_candidates_with_resume_cleanup, read_candidates_snapshot


EXTERNAL_GEEK_ID_PREFIX = "ext-"

_TIMESTAMP_FORMAT = "%Y%m%d_%H%M%S"
_RESUME_IMPORTED_AT_FORMAT = "%Y-%m-%d %H:%M:%S"


class ExternalImportPersistenceError(RuntimeError):
    """Raised when the candidate save fails after the resume copy was stored."""

    def __init__(self, message: str, *, copy_retained: bool) -> None:
        super().__init__(message)
        self.copy_retained = copy_retained


class ExternalImportDuplicateError(ValueError):
    """Raised when the latest locked snapshot already contains the candidate."""

    def __init__(self, message: str, *, copy_retained: bool = False) -> None:
        super().__init__(message)
        self.copy_retained = copy_retained


@dataclass(frozen=True)
class ExternalImportResult:
    """Persisted candidate snapshot plus the filtering outcome."""

    candidate: dict[str, Any]
    resume_text: str
    passed: bool
    score: int
    rejection_reason: str
    # 硬条件淘汰时剔除硬条件重算的参考匹配分（未淘汰时为 0，无参考意义）
    reference_score: int = 0


# 参考分口径：剔除全部硬条件约束后重跑规则筛选，只看基础分、技能与优先项匹配。
_REFERENCE_RULE_OVERRIDES: dict[str, Any] = {
    "edu": "不限",
    "min_exp": 0,
    "max_age": None,
    "gender": "",
    "work_location": "",
    "salary_min": None,
    "salary_max": None,
    "required_conditions": [],
    "tech_conditions": [],
}


def _reference_filter_outcome(
    resume_text: str,
    rule: dict[str, Any],
    structured_fields: dict[str, Any],
) -> tuple[int, dict[str, Any]]:
    """Re-run filtering with hard conditions neutralized for a reference score.

    硬条件淘汰的记录按存储约定 match_score 固定为 0；参考分让淘汰记录
    仍然携带技能/经验匹配度信息，供结果表、详情页和误杀核对展示。
    少数非规则驱动的淘汰（如简历明写"暂不考虑"）在中性规则下依然失败，
    此时参考分回落为 0。
    """
    scoring_rule = {**rule, **_REFERENCE_RULE_OVERRIDES}
    passed, score, details = filter_candidate(resume_text, scoring_rule, structured_fields)
    if not passed:
        return 0, {}
    return score, details


def new_external_geek_id() -> str:
    """Create a synthetic candidate ID that can never collide with BOSS IDs."""
    return f"{EXTERNAL_GEEK_ID_PREFIX}{uuid.uuid4().hex[:12]}"


# 渠道命名常把客户公司放在姓名之前（"编号_公司_姓名"）。疑似公司段特征：
# 常见城市名开头（"上海新致"）或含公司关键词，识别后跳过避免误取为姓名。
_COMPANY_CITY_PREFIXES = (
    "北京", "上海", "广州", "深圳", "杭州", "南京", "苏州", "成都", "武汉",
    "西安", "长沙", "合肥", "天津", "重庆", "青岛", "济南", "郑州", "无锡",
    "宁波", "佛山", "东莞",
)
_COMPANY_KEYWORDS = (
    "科技", "软件", "信息", "网络", "技术", "咨询", "集团", "股份", "有限",
    "公司", "电子", "通信", "生物", "医药", "金融", "控股", "实业", "发展",
)


def _looks_like_company_segment(segment: str) -> bool:
    """Heuristic: a Chinese segment naming a company rather than a person."""
    return segment.startswith(_COMPANY_CITY_PREFIXES) or any(
        keyword in segment for keyword in _COMPANY_KEYWORDS
    )


def _is_name_segment(segment: str) -> bool:
    """A 2-4 char pure Chinese segment that does not look like a company."""
    return bool(re.fullmatch(r"[一-龥]{2,4}", segment or "")) and not (
        _looks_like_company_segment(segment)
    )


def guess_name_from_filename(file_path: str | Path) -> str:
    """Best-effort candidate name guess from a resume file name."""
    stem = Path(file_path).stem.strip()
    # 噪声词全局清理（开头/结尾/中间的"简历""求职"等都移除，再取首个中文姓名段）；
    # 岗位词（如"研发工程师"）同样移除，避免 "Java研发工程师朱建杨简历" 误取"研发工程"。
    cleaned = re.sub(
        r"(?i)(个人简历|求职简历|实习生|工程师|架构师|简历|求职|应聘|"
        r"研发|开发|高级|资深|经理|主管|专员|实习|运维|测试|前端|后端|全栈|"
        r"resume|cv)",
        "",
        stem,
    )
    # 渠道文件名常带日期锚点，候选人姓名段紧邻日期：结尾独立日期段
    # “DIG-667_步步高_Siebel_谢小为_20170307”应取“谢小为”而非首个中文段
    # “步步高”；编号段内嵌日期（“RYXQ20170518-0069_上海新致_G6_姚漫”）同样
    # 视为锚点，此时姓名在锚点之后、公司段让位，应取“姚漫”。
    # 手机号等长数字段不是日期（须 19/20 开头 6-8 位）。
    segments = re.split(r"[_\s　\-–—.,，。()（）\[\]【】]+", cleaned)
    for idx, segment in enumerate(segments):
        is_date_anchor = bool(
            re.fullmatch(r"(?:19|20)\d{4,6}", segment or "")
            or re.search(r"(?:19|20)\d{6}", segment or "")
        )
        if not is_date_anchor:
            continue
        for prev in reversed(segments[:idx]):
            if _is_name_segment(prev):
                return prev
        for nxt in segments[idx + 1:]:
            if _is_name_segment(nxt):
                return nxt
        break  # 日期段前后都没有姓名段时回退逐段扫描
    # 无日期锚点：逐段找首个非公司姓名段（"南京Java-蒋彪"应跳过混合段
    # "南京Java"取"蒋彪"；"北京_张三"应跳过城市段"北京"），全部失败才裸取
    # 首个中文段兜底——此时文件名本身无姓名信息，取到什么都只能靠人工核对。
    for segment in segments:
        if _is_name_segment(segment):
            return segment
    match = re.search(r"[一-龥]{2,4}", cleaned)
    if match:
        return match.group(0)
    return ""


def find_external_duplicate(
    candidates: list[dict[str, Any]],
    *,
    name: str,
    job_uuid: Any,
    exclude_geek_id: str = "",
) -> dict[str, Any] | None:
    """Return an existing external candidate with the same name and job."""
    target_name = str(name or "").strip().casefold()
    target_job = normalize_job_uuid(job_uuid)
    excluded = str(exclude_geek_id or "").strip()
    if not target_name or not target_job:
        return None
    for candidate in candidates:
        if not is_external_candidate(candidate):
            continue
        if excluded and str(candidate.get("geek_id") or "") == excluded:
            continue
        if normalize_job_uuid(candidate.get("job_uuid")) != target_job:
            continue
        if str(candidate.get("name") or "").strip().casefold() == target_name:
            return candidate
    return None


def import_external_candidate(
    source_path: str | Path,
    *,
    name: str,
    job_name: str,
    rule: dict[str, Any],
    source_channel: str,
    source_note: str = "",
    candidates_path: str | Path,
    base_dir: str | Path,
    parser: Callable[[str | Path], str] = parse_resume_text,
    summary_info_extractor: Callable[[str], dict[str, Any]] | None = None,
    profile_enhancer: Callable[[str, dict[str, Any]], dict[str, Any] | None] | None = None,
    allow_duplicate: bool = False,
    now: datetime | None = None,
) -> ExternalImportResult:
    """Import one external-channel resume as a scored candidate record.

    The record carries a synthetic ``ext-`` geek ID and a managed resume
    copy, so low-score and hard-rejected imports are retained as user
    history instead of being dropped by save-time filtering.

    ``profile_enhancer``（可选）在正则画像之后、结构化钉定之前应用：
    返回 ``{"info", "filled", "conflicts", "error"}`` 合并结果；补全字段
    参与硬条件筛选，冲突保留规则值并在通过时转人工复核。返回 None 表示
    静默跳过，异常与非法返回转成 ``profile_ai_error`` 记录，不阻断导入。
    """
    clean_name = str(name or "").strip()
    if not clean_name:
        raise ValueError("候选人姓名不能为空")
    clean_job_name = str(job_name or "").strip()
    job_uuid = normalize_job_uuid(rule.get("job_uuid"))
    if not clean_job_name or not job_uuid:
        raise ValueError("岗位配置缺少稳定 ID，请重新保存岗位配置")
    clean_channel = str(source_channel or "").strip()
    if not clean_channel:
        raise ValueError("来源渠道不能为空")

    resume_text = parser(source_path)
    summary_info = (
        summary_info_extractor(resume_text) if summary_info_extractor else {}
    )
    enhanced = _apply_profile_enhancer(profile_enhancer, resume_text, summary_info)
    summary_info = enhanced["info"]
    structured_fields = _structured_fields_from_summary(summary_info)
    passed, score, details = filter_candidate(
        resume_text,
        rule,
        structured_fields,
    )
    reference: tuple[int, dict[str, Any]] | None = None
    if not passed:
        reference = _reference_filter_outcome(resume_text, rule, structured_fields)

    root = Path(base_dir)
    try:
        managed_resume = store_resume_copy(source_path, base_dir=root)
    except Exception as exc:
        raise ResumeCopyError(str(exc)) from exc

    moment = now or datetime.now()
    timestamp = moment.strftime(_TIMESTAMP_FORMAT)
    record = _build_candidate_record(
        clean_name,
        clean_job_name,
        job_uuid,
        resume_text,
        passed,
        score,
        details,
        summary_info,
        managed_resume,
        clean_channel,
        str(source_note or "").strip(),
        timestamp,
        moment.strftime(_RESUME_IMPORTED_AT_FORMAT),
        reference=reference,
        profile_enhancement=enhanced,
    )

    def append_record(candidates: list[dict[str, Any]]) -> int:
        if not allow_duplicate and find_external_duplicate(
            candidates,
            name=clean_name,
            job_uuid=job_uuid,
        ) is not None:
            raise ExternalImportDuplicateError(
                f"岗位「{clean_job_name}」下已存在同名外部候选人"
            )
        candidates.append(record)
        return 1

    try:
        saved, _cleanup = mutate_candidates_with_resume_cleanup(
            append_record,
            candidates_path,
            base_dir=root,
        )
    except ExternalImportDuplicateError as exc:
        copy_retained = _discard_new_resume_copy(managed_resume.reference, root)
        raise ExternalImportDuplicateError(
            str(exc), copy_retained=copy_retained
        ) from exc
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        copy_retained = True
        try:
            latest_candidates = read_candidates_snapshot(candidates_path)
            copy_retained = any(
                persisted.get("geek_id") == record["geek_id"]
                for persisted in latest_candidates
            )
        except (OSError, RuntimeError, TypeError, ValueError):
            pass
        if not copy_retained:
            copy_retained = _discard_new_resume_copy(managed_resume.reference, root)
        raise ExternalImportPersistenceError(str(exc), copy_retained=copy_retained) from exc

    if not saved:
        copy_retained = _discard_new_resume_copy(managed_resume.reference, root)
        raise ExternalImportPersistenceError(
            "候选人数据未发生变化，本次导入没有保存。",
            copy_retained=copy_retained,
        )

    return ExternalImportResult(
        candidate=record,
        resume_text=resume_text,
        passed=passed,
        score=score,
        rejection_reason="" if passed else str(details.get("reason") or "未通过筛选"),
        reference_score=reference[0] if reference else 0,
    )


def _discard_new_resume_copy(reference: str, root: Path) -> bool:
    """Best-effort cleanup; return whether the new copy may still be retained."""
    try:
        delete_managed_resume(reference, base_dir=root)
    except (OSError, UnmanagedResumePathError):
        return True
    return False


BATCH_STATUS_IMPORTED = "imported"
BATCH_STATUS_REJECTED = "rejected"
BATCH_STATUS_SKIPPED_DUPLICATE = "skipped_duplicate"
BATCH_STATUS_FAILED = "failed"

_BATCH_NAME_FALLBACK_LIMIT = 20


@dataclass(frozen=True)
class BatchImportItem:
    """Per-file outcome of a batch import run."""

    path: str
    name: str
    status: str
    score: int
    reason: str
    name_needs_review: bool
    candidate: dict[str, Any] | None
    resume_text: str


@dataclass(frozen=True)
class BatchImportSummary:
    """All per-file outcomes of one batch run; counts derive from items."""

    items: tuple[BatchImportItem, ...]
    stopped: bool

    def count(self, status: str) -> int:
        return sum(1 for item in self.items if item.status == status)


def import_external_candidates(
    paths: Iterable[str | Path],
    *,
    job_name: str,
    rule: dict[str, Any],
    source_channel: str,
    source_note: str = "",
    candidates_path: str | Path,
    base_dir: str | Path,
    parser: Callable[[str | Path], str] = parse_resume_text,
    summary_info_extractor: Callable[[str], dict[str, Any]] | None = None,
    profile_enhancer: Callable[[str, dict[str, Any]], dict[str, Any] | None] | None = None,
    name_resolver: Callable[[str | Path], str] = guess_name_from_filename,
    progress_callback: Callable[[int, int, BatchImportItem], None] | None = None,
    stop_event: Any | None = None,
    parse_workers: int = 5,
) -> BatchImportSummary:
    """Import multiple resumes: parse in parallel, persist serially.

    解析阶段用 ``parse_workers`` 路线程池并行（旧版 .doc 经本机 Word
    转换单份可达数十秒，并行显著缩短整批耗时）；单次事务按解析完成
    顺序串行消费，每份仍重读候选人快照查重，同批先完成的同名者同样
    触发跳过。``progress_callback(done, total, item)`` 在每份事务完成
    后于调用方线程触发；中断时取消尚未开始的解析，进行中的解析任其
    跑完并丢弃结果；汇总项始终按输入文件顺序排列。
    """
    path_list = [str(path) for path in paths]
    total = len(path_list)
    job_uuid = normalize_job_uuid(rule.get("job_uuid"))
    items: list[BatchImportItem] = []
    stopped = False

    def _is_stopped() -> bool:
        return stop_event is not None and stop_event.is_set()

    def _consume(path: str, item_parser: Callable[[str | Path], str]) -> None:
        name, needs_review = _resolve_batch_name(path, name_resolver)
        item = _import_one_for_batch(
            path,
            name,
            needs_review,
            job_name=job_name,
            rule=rule,
            job_uuid=job_uuid,
            source_channel=source_channel,
            source_note=source_note,
            candidates_path=candidates_path,
            base_dir=base_dir,
            parser=item_parser,
            summary_info_extractor=summary_info_extractor,
            profile_enhancer=profile_enhancer,
        )
        items.append(item)
        if progress_callback is not None:
            progress_callback(len(items), total, item)

    if total <= 1 or parse_workers <= 1:
        for path in path_list:
            if _is_stopped():
                stopped = True
                break
            _consume(path, parser)
    else:
        pool = ThreadPoolExecutor(max_workers=min(parse_workers, total))
        try:
            future_to_path = {
                pool.submit(_parse_safely, parser, path): path for path in path_list
            }
            pending = set(future_to_path)
            while pending and not stopped:
                if _is_stopped():
                    stopped = True
                    break
                done, pending = wait(pending, timeout=0.2, return_when=FIRST_COMPLETED)
                for future in done:
                    if _is_stopped():
                        stopped = True
                        break
                    _consume(future_to_path[future], _prefetched_parser(future.result()))
        finally:
            pool.shutdown(wait=False, cancel_futures=True)

    order = {path: index for index, path in enumerate(path_list)}
    items.sort(key=lambda item: order.get(item.path, 0))
    return BatchImportSummary(items=tuple(items), stopped=stopped)


def _parse_safely(
    parser: Callable[[str | Path], str],
    path: str,
) -> str | BaseException:
    """Run the parser in a worker thread, capturing any failure."""
    try:
        return parser(path)
    except Exception as exc:
        return exc


def _prefetched_parser(parsed: str | BaseException) -> Callable[[str | Path], str]:
    """Wrap a pre-parsed text (or its captured failure) as a parser callable."""
    def _parser(_path: str | Path) -> str:
        if isinstance(parsed, BaseException):
            raise parsed
        return parsed

    return _parser


def _resolve_batch_name(
    path: str,
    resolver: Callable[[str | Path], str],
) -> tuple[str, bool]:
    """Resolve a batch-entry name; fall back to the file stem when unknown."""
    try:
        guessed = str(resolver(path) or "").strip()
    except Exception:
        guessed = ""
    if guessed:
        return guessed, False
    stem = Path(path).stem.strip() or "未命名"
    return stem[:_BATCH_NAME_FALLBACK_LIMIT], True


# 岗位调整后需要清除的旧岗位语境字段：两轮 AI 评估结论和复核结论。
REASSIGN_CLEARED_FIELDS: tuple[str, ...] = (
    "resume_eval_adjustment",
    "resume_eval_reason",
    "resume_eval_model",
    "resume_eval_at",
    "resume_eval_dimension_scores",
    "llm_evaluated",
    "llm_error",
    "llm_adjustment",
    "llm_reason",
    "llm_model",
    "llm_hard_condition_verdict",
    "llm_hard_condition_findings",
    "llm_dimension_scores",
    "review_passed_at",
    "contact_approved_at",
)


@dataclass(frozen=True)
class JobReassignResult:
    """Field updates for moving an external candidate to another job."""

    updates: dict[str, Any]
    cleared_fields: tuple[str, ...]
    passed: bool
    score: int
    rejection_reason: str
    # 硬条件淘汰时剔除硬条件重算的参考匹配分（未淘汰时为 0）
    reference_score: int = 0


def reassign_external_candidate_job(
    candidate: dict[str, Any],
    *,
    new_job_name: str,
    new_rule: dict[str, Any],
    summary_info_extractor: Callable[[str], dict[str, Any]] | None = None,
    now: datetime | None = None,
) -> JobReassignResult:
    """Compute the updates for moving an external candidate to another job.

    Re-runs rule filtering on the stored resume text against the new rule.
    AI evaluations and the review decision were made in the old job's
    context, so they are cleared; feedback, follow-up and blacklist state
    stays untouched. The caller owns persistence and any re-evaluation.
    """
    if not is_external_candidate(candidate):
        raise ValueError("只有外部渠道候选人支持调整归属岗位")
    clean_job_name = str(new_job_name or "").strip()
    job_uuid = normalize_job_uuid(new_rule.get("job_uuid"))
    if not clean_job_name or not job_uuid:
        raise ValueError("目标岗位配置缺少稳定 ID，请重新保存岗位配置")
    if job_uuid == normalize_job_uuid(candidate.get("job_uuid")):
        raise ValueError("候选人已归属该岗位")

    resume_text = str(candidate.get("summary") or "")
    if len(resume_text.strip()) < 50:
        raise ValueError("该候选人缺少简历全文，无法按新岗位重新评分")

    summary_info = (
        summary_info_extractor(resume_text) if summary_info_extractor else {}
    )
    structured_fields = _structured_fields_from_summary(summary_info)
    passed, score, details = filter_candidate(
        resume_text,
        new_rule,
        structured_fields,
    )
    reference: tuple[int, dict[str, Any]] | None = None
    if not passed:
        reference = _reference_filter_outcome(resume_text, new_rule, structured_fields)

    moment = now or datetime.now()
    scoring = _scoring_fields(passed, score, details, reference=reference)
    updates: dict[str, Any] = {
        "job_uuid": job_uuid,
        "job_name": clean_job_name,
        "match_rule": clean_job_name,
        **scoring,
        "rule_score": scoring["rule_score"] if not passed else scoring["match_score"],
        "last_evaluated_at": moment.strftime(_TIMESTAMP_FORMAT),
    }
    return JobReassignResult(
        updates=updates,
        cleared_fields=REASSIGN_CLEARED_FIELDS,
        passed=passed,
        score=scoring["match_score"],
        rejection_reason="" if passed else str(details.get("reason") or "未通过筛选"),
        reference_score=reference[0] if reference else 0,
    )


# 「编辑候选人信息」可改的画像字段（写入记录顶层钉定值）
PROFILE_EDITABLE_FIELDS: tuple[str, ...] = (
    "gender", "age", "education", "exp_years",
    "salary", "city", "job_status", "school", "company",
)

# 参与规则筛选的画像字段：变化后必须重跑筛选；姓名/学校/公司只影响展示
_PROFILE_FILTERING_FIELDS: tuple[str, ...] = (
    "gender", "age", "education", "exp_years", "salary", "city", "job_status",
)

_PROFILE_EDUCATION_VALUES = ("博士", "硕士", "本科", "大专", "高中", "中专")
_PROFILE_JOB_STATUS_VALUES = ("离职", "在职", "应届", "在校", "暂不考虑")
_PROFILE_GENDER_VALUES = ("男", "女")
_PROFILE_TEXT_LIMIT = 40

_PROFILE_FIELD_NAMES = {
    "gender": "性别",
    "age": "年龄",
    "education": "学历",
    "exp_years": "工作年限",
    "salary": "期望薪资",
    "city": "期望城市",
    "job_status": "求职状态",
    "school": "毕业学校",
    "company": "最近公司",
}


@dataclass(frozen=True)
class ProfileUpdateResult:
    """Field updates from the edit-candidate-info dialog."""

    updates: dict[str, Any]
    cleared_fields: tuple[str, ...]
    # 是否重跑了规则筛选（画像或岗位变化）；未重筛时 passed/score 反映当前记录
    refiltered: bool
    job_changed: bool
    passed: bool
    score: int
    rejection_reason: str
    reference_score: int = 0


def _normalize_profile_edit_value(field: str, raw: Any) -> str:
    """Validate and normalize one editable profile field; empty means unknown."""
    value = str(raw or "").strip()
    if not value:
        return ""
    label = _PROFILE_FIELD_NAMES[field]
    if field == "gender":
        if value not in _PROFILE_GENDER_VALUES:
            raise ValueError("性别只能填：男 / 女")
        return value
    if field in ("age", "exp_years"):
        try:
            number = int(float(value))
        except (TypeError, ValueError):
            raise ValueError(f"{label}请填整数数字") from None
        upper = 80 if field == "age" else 50
        if number < 0 or number > upper:
            raise ValueError(f"{label}超出合理范围（0-{upper}）")
        return str(number)
    if field == "education":
        if value not in _PROFILE_EDUCATION_VALUES:
            raise ValueError("学历只能选：博士 / 硕士 / 本科 / 大专 / 高中 / 中专")
        return value
    if field == "job_status":
        if value not in _PROFILE_JOB_STATUS_VALUES:
            raise ValueError(
                "求职状态只能选：离职 / 在职 / 应届 / 在校 / 暂不考虑"
            )
        return value
    if field == "salary":
        if value == "面议":
            return value
        salary_min, _salary_max = _parse_candidate_salary_range(value)
        if salary_min is None:
            raise ValueError("期望薪资格式：15-25K、20K 或面议")
        return value
    return value[:_PROFILE_TEXT_LIMIT]


def update_external_candidate_profile(
    candidate: dict[str, Any],
    *,
    name: str,
    fields: dict[str, Any],
    candidates: list[dict[str, Any]],
    rule: dict[str, Any] | None = None,
    job_name: str = "",
    summary_info_extractor: Callable[[str], dict[str, Any]] | None = None,
    now: datetime | None = None,
) -> ProfileUpdateResult:
    """Compute updates for manual profile edits on an external candidate.

    ``fields`` 是对话框回传的全量表单值（未修改的字段回传当前记录值，
    空串表示清除该字段的识别结果）；变化检测以记录顶层钉定值为基准。
    三档语义：只改姓名/学校/公司（展示字段）直接保存不重筛；画像字段
    （性别/年龄/学历/年限/薪资/城市/求职状态）变化时用修正值钉定后重跑
    岗位规则筛选，已有简历评估保留（简历没变）；归属岗位变化时在画像钉定
    基础上按新岗位规则重筛并清除旧岗位评估与复核结论。反馈、跟进、黑名单
    始终保留；持久化与重新评估由调用方负责。
    """
    if not is_external_candidate(candidate):
        raise ValueError("只有外部渠道导入的候选人支持编辑信息")
    clean_name = str(name or "").strip()
    if not clean_name:
        raise ValueError("姓名不能为空")

    edited = {
        field: _normalize_profile_edit_value(field, fields.get(field))
        for field in PROFILE_EDITABLE_FIELDS
    }

    current_job_uuid = normalize_job_uuid(candidate.get("job_uuid"))
    target_job_name = str(job_name or "").strip()
    job_changed = bool(target_job_name) and target_job_name != str(
        candidate.get("job_name") or ""
    )
    target_job_uuid = current_job_uuid
    if job_changed:
        if not isinstance(rule, dict) or not rule:
            raise ValueError("目标岗位配置缺失，请刷新后重试")
        target_job_uuid = normalize_job_uuid(rule.get("job_uuid"))
        if not target_job_uuid:
            raise ValueError("目标岗位配置缺少稳定 ID，请重新保存岗位配置")
    else:
        target_job_name = str(candidate.get("job_name") or "")

    duplicate = find_external_duplicate(
        candidates,
        name=clean_name,
        job_uuid=target_job_uuid,
        exclude_geek_id=str(candidate.get("geek_id") or ""),
    )
    if duplicate is not None:
        raise ValueError(f"岗位「{target_job_name}」下已存在同名外部候选人")

    def _record_value(field: str) -> str:
        return str(candidate.get(field) or "").strip()

    changed_fields = {
        field for field in PROFILE_EDITABLE_FIELDS if edited[field] != _record_value(field)
    }
    filtering_changed = bool(changed_fields & set(_PROFILE_FILTERING_FIELDS))
    moment = now or datetime.now()

    updates: dict[str, Any] = {}
    if clean_name != str(candidate.get("name") or "").strip():
        updates["name"] = clean_name
    for field in ("school", "company"):
        if field in changed_fields:
            updates[field] = edited[field]
    # 人工修正取代 AI 痕迹：被编辑的字段从 AI 补全/冲突清单中移除
    for trace_key in ("profile_ai_filled", "profile_conflicts"):
        items = candidate.get(trace_key) or []
        kept = [
            item
            for item in items
            if not (isinstance(item, dict) and item.get("field") in changed_fields)
        ]
        if len(kept) != len(items):
            updates[trace_key] = kept

    if not filtering_changed and not job_changed:
        if not updates:
            raise ValueError("没有需要保存的修改")
        return ProfileUpdateResult(
            updates=updates,
            cleared_fields=(),
            refiltered=False,
            job_changed=False,
            passed=str(candidate.get("qualification_status") or "") != "rejected",
            score=int(candidate.get("match_score") or 0),
            rejection_reason="",
        )

    resume_text = str(candidate.get("summary") or "")
    if len(resume_text.strip()) < 50:
        raise ValueError("该候选人缺少简历全文，无法重新评分")
    if not isinstance(rule, dict) or not rule:
        raise ValueError("缺少岗位规则配置，无法重新评分")

    base_info = (
        summary_info_extractor(resume_text) if summary_info_extractor else {}
    )
    merged = {**base_info}
    # 用户修正值钉定（含空串 = 未识别，跳过对应硬条件检查）
    for key in ("gender", "age", "exp_years", "salary", "city", "job_status"):
        merged[key] = edited[key]
    structured_fields = _structured_fields_from_summary(merged)
    # 画像编辑的全量表单值全部显式钉定。空值表示人工确认“未识别”，
    # filter_candidate 必须跳过对应检查，不能再次从原文猜回旧值。
    structured_fields.update(
        {
            "gender": edited["gender"],
            "age": int(edited["age"]) if edited["age"] else None,
            "education": edited["education"],
            "exp_years": edited["exp_years"] or None,
            "job_status": edited["job_status"],
        }
    )

    passed, score, details = filter_candidate(resume_text, rule, structured_fields)
    reference: tuple[int, dict[str, Any]] | None = None
    if not passed:
        reference = _reference_filter_outcome(resume_text, rule, structured_fields)
    scoring = _scoring_fields(passed, score, details, reference=reference)
    rule_score = scoring.get("rule_score", scoring["match_score"])
    if passed and not job_changed and candidate.get("resume_eval_adjustment") is not None:
        try:
            adjustment = int(candidate.get("resume_eval_adjustment") or 0)
        except (TypeError, ValueError):
            adjustment = 0
        final_score = max(0, min(100, int(rule_score) + adjustment))
        scoring["match_score"] = final_score
        scoring["recommend_level"] = _recommend_level(final_score)
    updates.update(
        {
            "name": clean_name,
            **{field: edited[field] for field in PROFILE_EDITABLE_FIELDS},
            **scoring,
            "rule_score": rule_score,
            "last_evaluated_at": moment.strftime(_TIMESTAMP_FORMAT),
        }
    )
    cleared_fields: tuple[str, ...] = ()
    if job_changed:
        updates.update(
            {
                "job_uuid": target_job_uuid,
                "job_name": target_job_name,
                "match_rule": target_job_name,
            }
        )
        cleared_fields = REASSIGN_CLEARED_FIELDS
    return ProfileUpdateResult(
        updates=updates,
        cleared_fields=cleared_fields,
        refiltered=True,
        job_changed=job_changed,
        passed=passed,
        score=scoring["match_score"],
        rejection_reason="" if passed else str(details.get("reason") or "未通过筛选"),
        reference_score=reference[0] if reference else 0,
    )


def _import_one_for_batch(
    path: str,
    name: str,
    needs_review: bool,
    *,
    job_name: str,
    rule: dict[str, Any],
    job_uuid: str,
    source_channel: str,
    source_note: str,
    candidates_path: str | Path,
    base_dir: str | Path,
    parser: Callable[[str | Path], str],
    summary_info_extractor: Callable[[str], dict[str, Any]] | None,
    profile_enhancer: Callable[[str, dict[str, Any]], dict[str, Any] | None] | None = None,
) -> BatchImportItem:
    """Import one file for the batch, converting failures into items."""
    duplicate = find_external_duplicate(
        read_candidates_snapshot(candidates_path),
        name=name,
        job_uuid=job_uuid,
    )
    if duplicate is not None:
        return BatchImportItem(
            path=path,
            name=name,
            status=BATCH_STATUS_SKIPPED_DUPLICATE,
            score=0,
            reason="同岗位已存在同名外部候选人，已跳过",
            name_needs_review=needs_review,
            candidate=None,
            resume_text="",
        )
    try:
        outcome = import_external_candidate(
            path,
            name=name,
            job_name=job_name,
            rule=rule,
            source_channel=source_channel,
            source_note=source_note,
            candidates_path=candidates_path,
            base_dir=base_dir,
            parser=parser,
            summary_info_extractor=summary_info_extractor,
            profile_enhancer=profile_enhancer,
        )
    except ExternalImportDuplicateError as exc:
        suffix = "；简历副本清理失败，请运行存储体检" if exc.copy_retained else ""
        return BatchImportItem(
            path=path,
            name=name,
            status=BATCH_STATUS_SKIPPED_DUPLICATE,
            score=0,
            reason=f"同岗位已存在同名外部候选人，已跳过{suffix}",
            name_needs_review=needs_review,
            candidate=None,
            resume_text="",
        )
    except ExternalImportPersistenceError as exc:
        suffix = "；简历副本已保留" if exc.copy_retained else ""
        return BatchImportItem(
            path=path,
            name=name,
            status=BATCH_STATUS_FAILED,
            score=0,
            reason=f"保存失败：{exc}{suffix}",
            name_needs_review=needs_review,
            candidate=None,
            resume_text="",
        )
    except Exception as exc:
        return BatchImportItem(
            path=path,
            name=name,
            status=BATCH_STATUS_FAILED,
            score=0,
            reason=str(exc),
            name_needs_review=needs_review,
            candidate=None,
            resume_text="",
        )
    if not outcome.passed:
        return BatchImportItem(
            path=path,
            name=name,
            status=BATCH_STATUS_REJECTED,
            score=outcome.reference_score,
            reason=outcome.rejection_reason,
            name_needs_review=needs_review,
            candidate=outcome.candidate,
            resume_text="",
        )
    low_score_reason = ""
    if outcome.score < SCORE_THRESHOLD_PASS:
        low_score_reason = f"评分低于 {SCORE_THRESHOLD_PASS} 分，记录已进入淘汰记录"
    conflict_reason = ""
    conflicts = (outcome.candidate or {}).get("profile_conflicts") or []
    if conflicts:
        labels = "、".join(
            str(item.get("label") or item.get("field") or "画像字段")
            for item in conflicts
            if isinstance(item, dict)
        )
        conflict_reason = f"AI 画像与规则不一致（{labels}），已转待复核"
    return BatchImportItem(
        path=path,
        name=name,
        status=BATCH_STATUS_IMPORTED,
        score=outcome.score,
        reason="；".join(part for part in (low_score_reason, conflict_reason) if part),
        name_needs_review=needs_review,
        candidate=outcome.candidate,
        resume_text=outcome.resume_text,
    )


def _structured_fields_from_summary(summary_info: dict[str, Any]) -> dict[str, Any]:
    """Map the extracted profile to ``filter_candidate`` structured inputs.

    Imported resumes are free-form text where the plain regex fallbacks
    misfire — an education line like "2014-2018" matches the optional-K
    salary pattern and gets read as a 2014K expectation. Salary and city are
    therefore always pinned from the extractor; an empty value means
    "unknown" and skips that check instead of re-scanning the resume text.
    """
    fields: dict[str, Any] = {}
    salary_min, _salary_max = _parse_candidate_salary_range(
        str(summary_info.get("salary") or "")
    )
    fields["salary_min"] = salary_min
    fields["city"] = str(summary_info.get("city") or "").strip()
    exp_years = str(summary_info.get("exp_years") or "").strip()
    if exp_years:
        fields["exp_years"] = exp_years
    age_raw = str(summary_info.get("age") or "").strip()
    if age_raw:
        try:
            fields["age"] = int(float(age_raw))
        except (TypeError, ValueError):
            pass
    gender = str(summary_info.get("gender") or "").strip()
    if gender:
        fields["gender"] = gender
    job_status = str(summary_info.get("job_status") or "").strip()
    if job_status:
        fields["job_status"] = job_status
    return fields


def _scoring_fields(
    passed: bool,
    score: int,
    details: dict[str, Any],
    *,
    reference: tuple[int, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Map a filtering outcome to the score/qualification record fields.

    淘汰记录按存储约定 match_score 固定为 0；reference（剔除硬条件后的
    重跑结果）提供 rule_score 与评分拆解等展示字段，让淘汰原因之外仍能
    看到技能/经验匹配度。
    """
    if passed:
        if score >= SCORE_THRESHOLD_STRONG:
            recommend_level = "强烈推荐"
        elif score >= SCORE_THRESHOLD_RECOMMEND:
            recommend_level = "推荐"
        elif score >= SCORE_THRESHOLD_PASS:
            recommend_level = "待定"
        else:
            recommend_level = "未通过"
        match_score = score
        qualification_status = details.get("qualification_status", "qualified")
        qualification_reasons = details.get("qualification_reasons", [])
    else:
        recommend_level = "未通过"
        match_score = 0
        qualification_status = "rejected"
        qualification_reasons = [str(details.get("reason") or "未通过筛选")]
    display_details = details
    if not passed and reference is not None and reference[1]:
        display_details = reference[1]
    fields = {
        "match_score": match_score,
        "recommend_level": recommend_level,
        "qualification_status": qualification_status,
        "qualification_reasons": qualification_reasons,
        "qualification_evidence": details.get("qualification_evidence", []),
        "skill_matches": display_details.get("skill_matches", []),
        "skill_match_ratio": (
            f"{display_details.get('skill_matched_count', 0)}/{display_details.get('skill_total', 0)}"
        ),
        "score_breakdown": display_details.get("score_breakdown", {}),
        "score_explanation": display_details.get("score_explanation", []),
        "keyword_evidence": display_details.get("keyword_evidence", []),
        "risk_flags": details.get("risk_flags", []),
        "manual_review_required": bool(details.get("manual_review_required")),
        "auto_greet_blocked_reason": details.get("auto_greet_blocked_reason", ""),
    }
    if not passed and reference is not None:
        fields["rule_score"] = reference[0]
    return fields


def _recommend_level(score: int) -> str:
    """Return the persisted recommendation label for one final score."""
    if score >= SCORE_THRESHOLD_STRONG:
        return "强烈推荐"
    if score >= SCORE_THRESHOLD_RECOMMEND:
        return "推荐"
    if score >= SCORE_THRESHOLD_PASS:
        return "待定"
    return "未通过"


def _apply_profile_enhancer(
    enhancer: Callable[[str, dict[str, Any]], dict[str, Any] | None] | None,
    resume_text: str,
    summary_info: dict[str, Any],
) -> dict[str, Any]:
    """Apply the optional profile enhancer, normalizing its failure modes.

    始终返回 ``{"info", "filled", "conflicts", "error"}``：enhancer 缺失或
    返回 None 表示不增强；抛异常或返回非法结构转成 error，画像保持正则
    结果。enhancer 契约中的 ``info`` 是正则/AI 合并后的完整画像。
    """
    base: dict[str, Any] = {
        "info": dict(summary_info),
        "filled": [],
        "conflicts": [],
        "error": "",
    }
    if enhancer is None:
        return base
    try:
        result = enhancer(resume_text, dict(summary_info))
    except Exception as exc:
        base["error"] = str(exc)[:200]
        return base
    if result is None:
        return base
    if not isinstance(result, dict) or not isinstance(result.get("info"), dict):
        base["error"] = "AI 增强返回格式异常，已按规则识别结果导入"
        return base
    merged_info = dict(summary_info)
    merged_info.update(result["info"])
    filled = result.get("filled")
    conflicts = result.get("conflicts")
    return {
        "info": merged_info,
        "filled": [item for item in filled if isinstance(item, dict)] if isinstance(filled, list) else [],
        "conflicts": [item for item in conflicts if isinstance(item, dict)] if isinstance(conflicts, list) else [],
        "error": str(result.get("error") or "")[:200],
    }


def _apply_profile_enhancement_to_record(
    record: dict[str, Any],
    enhanced: dict[str, Any],
    passed: bool,
) -> None:
    """Write profile-enhancement traces onto the built candidate record.

    冲突只记录、保留规则值：通过筛选时转 ``manual_review`` 等人工核对；
    淘汰时只追加审计说明，不改变淘汰结论。``manual_review_required`` 由
    保存层按 ``qualification_status`` 自动补齐，这里不写。
    """
    filled = enhanced.get("filled") or []
    conflicts = enhanced.get("conflicts") or []
    error = str(enhanced.get("error") or "")
    if filled:
        record["profile_ai_filled"] = filled
    if conflicts:
        record["profile_conflicts"] = conflicts
    if error:
        record["profile_ai_error"] = error

    reasons = [str(reason) for reason in record.get("qualification_reasons") or []]
    original_count = len(reasons)
    if conflicts:
        for item in conflicts:
            label = str(item.get("label") or item.get("field") or "画像字段")
            reasons.append(
                f"AI 画像与规则识别不一致：{label}"
                f"（规则 {item.get('rule')} / AI {item.get('ai')}），已保留规则值"
            )
        if passed:
            record["qualification_status"] = "manual_review"
    if not passed and filled:
        labels = "、".join(
            str(item.get("label") or item.get("field") or "画像字段") for item in filled
        )
        reasons.append(
            f"注意：本次筛选使用了 AI 补全的画像字段（{labels}），淘汰结论请人工核对"
        )
    if len(reasons) != original_count:
        record["qualification_reasons"] = reasons


def _build_candidate_record(
    name: str,
    job_name: str,
    job_uuid: str,
    resume_text: str,
    passed: bool,
    score: int,
    details: dict[str, Any],
    summary_info: dict[str, Any],
    managed_resume: Any,
    source_channel: str,
    source_note: str,
    timestamp: str,
    resume_imported_at: str,
    *,
    reference: tuple[int, dict[str, Any]] | None = None,
    profile_enhancement: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build one external candidate record mirroring the scan record shape."""
    scoring = _scoring_fields(passed, score, details, reference=reference)

    record: dict[str, Any] = {
        "schema_version": CANDIDATE_SCHEMA_VERSION,
        "geek_id": new_external_geek_id(),
        "name": name,
        "summary": resume_text,
        "job_id": "",
        "job_uuid": job_uuid,
        "job_name": job_name,
        "salary": summary_info.get("salary", ""),
        "gender": summary_info.get("gender", ""),
        "age": summary_info.get("age", ""),
        "exp_years": summary_info.get("exp_years", ""),
        "education": summary_info.get("education", ""),
        "city": summary_info.get("city", ""),
        "job_status": summary_info.get("job_status", ""),
        "company": summary_info.get("company", ""),
        "school": summary_info.get("school", ""),
        "match_rule": job_name,
        **scoring,
        "batch_timestamp": timestamp,
        "first_seen_at": timestamp,
        "last_evaluated_at": timestamp,
        "followup_status": "未沟通",
        "greet_sent": False,
        "source": EXTERNAL_CANDIDATE_SOURCE,
        "source_channel": source_channel,
        "resume_file": managed_resume.reference,
        "resume_artifact_id": managed_resume.artifact_id,
        "resume_original_name": managed_resume.original_name,
        "resume_imported_at": resume_imported_at,
    }
    if source_note:
        record["source_note"] = source_note
    if profile_enhancement:
        _apply_profile_enhancement_to_record(record, profile_enhancement, passed)
    return record
