"""Education certificate and CHSI workflow control without Tk dependencies."""
from __future__ import annotations

from collections.abc import Callable, Mapping, MutableMapping, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
import re
from typing import Any


EducationItem = dict[str, Any]


class EducationBrowserSnapshotCache:
    """Share short-lived read-only browser observations under the caller's lock."""

    def __init__(self, clock: Callable[[], float], ttl: float = 0.8):
        self.clock = clock
        self.ttl = ttl
        self.values: dict[Any, tuple[float, Any, Exception | None]] = {}

    def read(self, key: Any, reader: Callable[[], Any]) -> Any:
        now = self.clock()
        cached = self.values.get(key)
        if cached is None or cached[0] <= now:
            self.values = {k: value for k, value in self.values.items() if value[0] > now}
            try:
                cached = (now + self.ttl, reader(), None)
            except Exception as error:
                cached = (now + self.ttl, None, error)
            self.values[key] = cached
        if cached[2] is not None:
            raise cached[2]
        return cached[1]

EDUCATION_CAPTCHA_MAX_ATTEMPTS = 5
EDUCATION_RESULT_READY_STATUS = "核验结果已生成"
EDUCATION_RESULT_NOT_FOUND_STATUS = "未查询到记录"
EDUCATION_WAITING_FOR_SCAN_STATUS = "等待扫码"
EDUCATION_QR_EXPIRED_STATUS = "二维码已过期"
EDUCATION_FORM_EMPTY_STATUS = "表单待填写"
EDUCATION_CAPTCHA_RETRY_STATUSES = frozenset({
    "待人工验证",
    "验证码识别失败",
})
EDUCATION_VERIFICATION_PENDING_STATUSES = frozenset({
    "待人工验证",
    "验证码识别失败",
    "已提交查询",
    EDUCATION_WAITING_FOR_SCAN_STATUS,
    "结果未确认",
    EDUCATION_QR_EXPIRED_STATUS,
})
EDUCATION_VERIFICATION_STARTED_STATUSES = frozenset({
    *EDUCATION_VERIFICATION_PENDING_STATUSES,
    EDUCATION_RESULT_READY_STATUS,
    EDUCATION_RESULT_NOT_FOUND_STATUS,
})


@dataclass(frozen=True)
class EducationImportBatch:
    """Validated queue additions and rejected filenames."""

    items: Mapping[str, EducationItem]
    invalid_files: tuple[str, ...]
    next_counter: int


@dataclass(frozen=True)
class ChsiPreparation:
    """Validated CHSI requests; invalid items are already marked in the snapshot."""

    prepared: tuple[tuple[str, str, str], ...]
    invalid_ids: tuple[str, ...]


@dataclass(frozen=True)
class CaptchaResult:
    successful: bool
    status: str


@dataclass(frozen=True)
class EducationActionStates:
    """Enabled states for the education page's coordinated actions."""

    recognize: bool
    verify: bool
    screenshot: bool
    retry_captcha: bool


@dataclass(frozen=True)
class EducationQueueStatusSummary:
    """Recognition and CHSI verification counts for the queue header."""

    total: int
    recognition_pending: int
    recognizing: int
    recognition_failed: int
    manual_review: int
    recognized: int
    manually_completed: int
    verification_not_started: int
    verification_processing: int
    waiting_scan: int
    waiting_result: int
    qr_expired: int
    result_ready: int
    result_not_found: int
    verification_attention: int
    verification_failed: int

    @property
    def information_ready(self) -> int:
        """Return records ready for CHSI, regardless of automatic/manual origin."""
        return self.recognized + self.manually_completed


@dataclass(frozen=True)
class ScreenshotItemResult:
    """One candidate's batch screenshot outcome or transient progress state."""

    item_id: str
    status: str
    detail: str
    path: str = ""


@dataclass(frozen=True)
class ScreenshotBatchResult:
    """Final ordered outcomes for a repeatable CHSI screenshot run."""

    items: tuple[ScreenshotItemResult, ...]

    @property
    def saved(self) -> int:
        return sum(item.status == "已保存" for item in self.items)

    @property
    def skipped(self) -> int:
        return sum(item.status == "已存在" for item in self.items)

    @property
    def pending(self) -> int:
        return sum(item.status in {"未打开", "待结果页"} for item in self.items)

    @property
    def failed(self) -> int:
        return sum(
            item.status in {"页面已关闭", "文件异常", "截图失败"}
            for item in self.items
        )


class EducationController:
    """Coordinate certificate recognition and captcha state as plain data."""

    @staticmethod
    def duplicate_certificate_ids(items: Mapping[str, Mapping[str, Any]]) -> dict[str, tuple[str, ...]]:
        """Find possible duplicates by explicit type and normalized number, without merging records."""
        groups: dict[tuple[str, str], list[str]] = {}
        for item_id, item in items.items():
            kind = item.get("certificate_type")
            number = re.sub(r"\s+", "", str(item.get("certificate_number") or "")).upper()
            if kind not in {"education", "degree"} or not number:
                continue
            groups.setdefault((kind, number), []).append(item_id)
        return {
            item_id: tuple(peer for peer in group if peer != item_id)
            for group in groups.values() if len(group) > 1
            for item_id in group
        }

    @staticmethod
    def fields_ready(item: Mapping[str, Any]) -> bool:
        return bool(
            str(item.get("name") or "").strip()
            and str(item.get("certificate_number") or "").strip()
            and item.get("certificate_type", "education") in {"education", "degree"}
            and not item.get("critical_conflicts")
            and item.get("status") != "待人工确认"
        )

    @staticmethod
    def verification_item_ids(items: Mapping[str, Mapping[str, Any]]) -> tuple[str, ...]:
        return tuple(
            key for key, item in items.items()
            if EducationController.fields_ready(item)
            and item.get("status") not in {
                *EDUCATION_VERIFICATION_PENDING_STATUSES, EDUCATION_RESULT_READY_STATUS,
                "打开中", "识别中", "排队中", "识别验证码中...",
            }
            and not str(item.get("status", "")).startswith("正在")
        )

    @staticmethod
    def recognition_item_ids(items: Mapping[str, Mapping[str, Any]], *, force: bool = False) -> tuple[str, ...]:
        return tuple(
            key for key, item in items.items()
            if item.get("status") not in EDUCATION_VERIFICATION_STARTED_STATUSES
            and (force or (
                item.get("status", "待识别") in {"待识别", "识别失败", "校验失败", "待人工确认"}
                and not item.get("manually_edited")
            ))
        )

    @staticmethod
    def confirm_fields(item: EducationItem, validator: Callable[[str, str], Any]) -> None:
        if item.get("certificate_type") not in {"education", "degree"}:
            raise ValueError("请先选择证书类型")
        validator(str(item.get("name") or ""), str(item.get("certificate_number") or ""))
        item.update(status="信息已修改", critical_conflicts=(), manually_edited=True,
                    detail="关键信息已人工核对，可执行学信网验证", warnings="")
        item["manual_fields"] = {field: item[field] for field in ("name", "certificate_number", "certificate_type")}

    @staticmethod
    def result_number_matches(text: str, item: Mapping[str, Any], *, bound: bool) -> bool:
        """Reject explicit mismatches; masked or absent numbers need a bound query."""
        number = re.sub(r"\s+", "", str(item.get("certificate_number") or ""))
        if not number:
            return False
        compact = re.sub(r"\s+", "", text)
        visible = re.findall(
            r"(?:证书编号|证书号码|电子注册号)[：:]?([0-9A-Za-z*＊×•·]{6,30})", compact,
        )
        if visible:
            for value in visible:
                if any(mark in value for mark in "*＊×•·"):
                    pattern = "".join("." if char in "*＊×•·" else re.escape(char) for char in value)
                    if not bound or re.fullmatch(pattern, number, flags=re.IGNORECASE) is None:
                        return False
                elif value.lower() != number.lower():
                    return False
            return True
        if re.search(r"(?<![0-9A-Za-z])" + re.escape(number) + r"(?![0-9A-Za-z])", compact, re.IGNORECASE):
            return True
        return bound

    @staticmethod
    def summarize_queue_statuses(
        items: Mapping[str, Mapping[str, Any]],
    ) -> EducationQueueStatusSummary:
        """Classify recognition and CHSI stages without conflating them."""
        item_states = []
        for item in items.values():
            status = str(item.get("status") or "待识别")
            fields_ready = bool(
                str(item.get("name") or "").strip()
                and str(item.get("certificate_number") or "").strip()
            )
            manually_edited = bool(item.get("manually_edited")) or (
                status == "信息已修改"
            )
            item_states.append((status, fields_ready, manually_edited))
        statuses = [state[0] for state in item_states]
        total = len(statuses)
        recognition_pending = statuses.count("待识别") + statuses.count("排队中")
        recognizing = statuses.count("识别中")
        recognition_failed = sum(
            status in {"识别失败", "校验失败"}
            for status in statuses
        )
        manual_review = statuses.count("待人工确认") + sum(
            status == "信息已修改" and not fields_ready
            for status, fields_ready, _manually_edited in item_states
        )
        manually_completed = sum(
            manually_edited
            and fields_ready
            and status not in {
                "待识别",
                "识别中",
                "识别失败",
                "校验失败",
                "待人工确认",
            }
            for status, fields_ready, manually_edited in item_states
        )
        recognized = max(
            0,
            total
            - recognition_pending
            - recognizing
            - recognition_failed
            - manual_review
            - manually_completed,
        )
        return EducationQueueStatusSummary(
            total=total,
            recognition_pending=recognition_pending,
            recognizing=recognizing,
            recognition_failed=recognition_failed,
            manual_review=manual_review,
            recognized=recognized,
            manually_completed=manually_completed,
            verification_not_started=sum(
                status in {"已识别", "识别成功"}
                or (status == "信息已修改" and fields_ready)
                for status, fields_ready, _manually_edited in item_states
            ),
            verification_processing=sum(
                status in {"打开中", "识别验证码中..."}
                or status.startswith("正在")
                for status in statuses
            ),
            waiting_scan=sum(
                status in {"已提交查询", EDUCATION_WAITING_FOR_SCAN_STATUS}
                for status in statuses
            ),
            waiting_result=statuses.count("结果未确认"),
            qr_expired=statuses.count(EDUCATION_QR_EXPIRED_STATUS),
            result_ready=statuses.count(EDUCATION_RESULT_READY_STATUS),
            result_not_found=statuses.count(EDUCATION_RESULT_NOT_FOUND_STATUS),
            verification_attention=sum(
                status in EDUCATION_CAPTCHA_RETRY_STATUSES
                for status in statuses
            ),
            verification_failed=sum(
                status in {"打开失败", EDUCATION_FORM_EMPTY_STATUS}
                for status in statuses
            ),
        )

    @staticmethod
    def resolve_api_config(api_config: Mapping[str, Any]) -> dict[str, Any]:
        education_ref = api_config.get("education_model_ref")
        if isinstance(education_ref, Mapping) and education_ref.get("model"):
            return dict(education_ref)
        return dict(api_config)

    @staticmethod
    def prepare_import(
        paths: Sequence[str],
        existing_items: Mapping[str, Mapping[str, Any]],
        start_counter: int,
        *,
        validator: Callable[[str], Path],
        is_pdf: Callable[[Path], bool],
    ) -> EducationImportBatch:
        """Validate and de-duplicate imported files without touching widgets."""
        existing_paths = {
            str(Path(item["path"]).resolve()).lower()
            for item in existing_items.values()
        }
        counter = start_counter
        additions: dict[str, EducationItem] = {}
        invalid_files: list[str] = []
        for raw_path in paths:
            try:
                path = validator(raw_path)
            except ValueError:
                invalid_files.append(Path(raw_path).name)
                continue
            normalized = str(path.resolve()).lower()
            if normalized in existing_paths:
                continue
            existing_paths.add(normalized)
            counter += 1
            item_id = f"education_{counter}"
            additions[item_id] = {
                "path": str(path),
                "is_pdf": is_pdf(path),
                "name": "",
                "certificate_number": "",
                "certificate_type": "unknown",
                "school": "",
                "major": "",
                "auto_rotation": 0,
                "status": "待识别",
                "detail": "",
                "warnings": "",
                "screenshot_status": "待识别",
                "screenshot_detail": "证书尚未识别，暂不能生成结果截图",
                "screenshot_path": "",
            }
        return EducationImportBatch(additions, tuple(invalid_files), counter)

    @staticmethod
    def screenshot_readiness(
        item: Mapping[str, Any],
    ) -> tuple[str, str]:
        """Derive screenshot readiness from the current validation stage."""
        primary_status = str(item.get("status") or "待识别")
        name = str(item.get("name") or "").strip()
        certificate_number = str(
            item.get("certificate_number") or ""
        ).strip()
        if primary_status == "识别中":
            return "待识别", "正在识别证书，暂不能生成结果截图"
        if not name or not certificate_number:
            return "待识别", "姓名或证书编号尚未识别完整"
        if primary_status in {"打开中", "识别验证码中..."} or primary_status.startswith(
            "正在"
        ):
            return "验证中", "正在打开学信网或识别验证码"
        if primary_status == "已提交查询":
            return "待结果", "查询已提交，正在等待学信网进入扫码页面"
        if primary_status == EDUCATION_WAITING_FOR_SCAN_STATUS:
            return "待结果", "请使用手机扫码确认，随后等待最终学历或学位查询结果"
        if primary_status == EDUCATION_QR_EXPIRED_STATUS:
            return "待结果", "扫码二维码已过期，请刷新二维码后继续扫码"
        if primary_status == "结果未确认":
            return "待结果", "验证码已提交，正在监测二维码或最终查询结果"
        if primary_status == EDUCATION_RESULT_READY_STATUS:
            return "待截图", "已检测到最终学历或学位查询结果，可执行批量截图"
        if primary_status == EDUCATION_RESULT_NOT_FOUND_STATUS:
            return "无需截图", "学信网未查询到记录，不进入截图流程"
        if primary_status in {
            "待人工验证",
            "验证码识别失败",
            "识别失败",
            "校验失败",
            "打开失败",
            EDUCATION_FORM_EMPTY_STATUS,
        }:
            return "待验证", "学信网验证尚未完成，暂不能截图"
        return "待验证", "证书已识别，尚未完成学信网验证"

    @staticmethod
    def result_ready_item_ids(
        items: Mapping[str, Mapping[str, Any]],
    ) -> tuple[str, ...]:
        """Return queued records whose final CHSI result page was detected."""
        return tuple(
            item_id
            for item_id, item in items.items()
            if item.get("status") == EDUCATION_RESULT_READY_STATUS
        )

    @staticmethod
    def captcha_retry_item_ids(
        items: Mapping[str, Mapping[str, Any]],
    ) -> tuple[str, ...]:
        """Return all queue records eligible for a captcha-only retry."""
        return tuple(
            item_id
            for item_id, item in items.items()
            if item.get("status") in EDUCATION_CAPTCHA_RETRY_STATUSES
        )

    @staticmethod
    def action_states(
        items: Mapping[str, Mapping[str, Any]],
        *,
        recognition_running: bool = False,
        screenshot_running: bool = False,
    ) -> EducationActionStates:
        """Derive every education action from one consistent state snapshot.

        Recognition stays locked after CHSI verification has begun so a new
        model result cannot overwrite the name or certificate number already
        submitted to the browser.  A browser/opening failure is deliberately
        not a locked state, which makes both recognition and verification
        usable again after an interrupted attempt.
        """
        statuses = {
            str(item.get("status") or "待识别")
            for item in items.values()
        }
        verification_active = any(
            status in {"打开中", "识别验证码中..."}
            or status.startswith("正在")
            for status in statuses
        )
        busy = bool(
            recognition_running
            or screenshot_running
            or verification_active
        )
        has_items = bool(items)
        has_result = EDUCATION_RESULT_READY_STATUS in statuses
        can_reconcile_browser_result = bool(statuses & {
            "已提交查询",
            EDUCATION_WAITING_FOR_SCAN_STATUS,
            "结果未确认",
        })
        verification_fields_ready = bool(EducationController.verification_item_ids(items))
        retry_ids = EducationController.captcha_retry_item_ids(items)
        return EducationActionStates(
            recognize=(has_items and not busy and bool(EducationController.recognition_item_ids(items, force=True))),
            verify=(
                has_items
                and verification_fields_ready
                and not busy
            ),
            screenshot=(
                (has_result or can_reconcile_browser_result)
                and not recognition_running
                and not screenshot_running
                and not verification_active
            ),
            retry_captcha=(
                bool(retry_ids)
                and not recognition_running
                and not screenshot_running
                and not verification_active
            ),
        )

    @staticmethod
    def wait_for_result_page(
        page: Any,
        expected_name: str,
        *,
        page_alive: Callable[[Any], bool],
        read_text: Callable[[Any], str],
        is_result_text: Callable[[str, str], bool],
        sleep: Callable[[float], None],
        max_checks: int | None = 900,
        interval_seconds: float = 2.0,
        max_unavailable_checks: int = 15,
        should_stop: Callable[[], bool] = lambda: False,
        recover_page: Callable[[Any], bool] | None = None,
        poll_interval: Callable[[int], float] | None = None,
    ) -> bool:
        """Poll a CHSI tab, tolerating transient unreadability during navigation."""
        checks = max(1, int(max_checks)) if max_checks is not None else None
        unavailable_limit = max(1, int(max_unavailable_checks))
        unavailable_checks = 0
        check_no = 0
        while checks is None or check_no < checks:
            if should_stop():
                return False
            try:
                alive = page_alive(page)
                if alive:
                    text = read_text(page)
                    if should_stop():
                        return False
                    if is_result_text(text, expected_name):
                        return True
            except Exception:
                alive = False
            if not alive:
                unavailable_checks += 1
                if unavailable_checks == 2 and recover_page is not None and not should_stop():
                    try:
                        if recover_page(page):
                            continue
                    except Exception:
                        pass
                if unavailable_checks >= unavailable_limit:
                    return False
            else:
                unavailable_checks = 0
            check_no += 1
            if checks is None or check_no < checks:
                delay = poll_interval(check_no) if poll_interval is not None and not unavailable_checks else interval_seconds
                # Keep exit/removal responsive even during low-frequency monitoring.
                remaining = max(0.0, float(delay))
                while remaining > 0 and not should_stop():
                    step = min(1.0, remaining)
                    sleep(step)
                    remaining -= step
        return False

    @staticmethod
    def recognize_documents(
        items: Mapping[str, Mapping[str, Any]],
        item_ids: Sequence[str],
        config: Mapping[str, Any],
        api_key: str,
        *,
        recognize_image: Callable[..., Any],
        recognize_pdf: Callable[..., Any],
        max_workers: int = 3,
        on_result: Callable[[str, Any | None, str], None] | None = None,
        on_stage: Callable[[str, str, int], None] | None = None,
        stop_event: Any = None,
    ) -> dict[str, tuple[Any | None, str]]:
        """Recognize concurrently and emit each plain result as it completes."""
        selected = {
            item_id: dict(items[item_id])
            for item_id in item_ids
            if item_id in items
        }
        results: dict[str, tuple[Any | None, str]] = {}

        def recognize_one(item_id: str, item: Mapping[str, Any]) -> Any:
            if stop_event is not None and stop_event.is_set():
                raise RuntimeError("识别已停止")
            if on_stage is not None:
                on_stage(item_id, "正在识别", 1)
            path = item["path"]
            image_kwargs: dict[str, Any] = {}
            if stop_event is not None:
                image_kwargs["stop_event"] = stop_event
            if on_stage is not None:
                image_kwargs["on_progress"] = lambda stage, percent: on_stage(
                    item_id, stage, percent
                )
            if item.get("recognition_rotation") in (0, 90, 180, 270):
                image_kwargs["rotation_override"] = item[
                    "recognition_rotation"
                ]
            if item.get("is_pdf"):
                return recognize_pdf(path, dict(config), api_key, **image_kwargs)
            return recognize_image(
                path,
                dict(config),
                api_key,
                **image_kwargs,
            )

        workers = min(max(1, max_workers), max(1, len(selected)))
        try:
            with ThreadPoolExecutor(max_workers=workers) as executor:
                futures = {
                    executor.submit(recognize_one, item_id, item): item_id
                    for item_id, item in selected.items()
                }
                for future in as_completed(futures):
                    item_id = futures[future]
                    if stop_event is not None and stop_event.is_set():
                        for pending in futures:
                            pending.cancel()
                    if future.cancelled():
                        results[item_id] = (None, "识别已停止")
                        if on_result is not None:
                            on_result(item_id, None, "识别已停止")
                        continue
                    try:
                        results[item_id] = (future.result(), "")
                    except Exception as exc:
                        results[item_id] = (None, str(exc))
                    if on_result is not None:
                        result, error_text = results[item_id]
                        on_result(item_id, result, error_text)
        except Exception as exc:
            error = str(exc)
            for item_id in selected:
                results.setdefault(item_id, (None, error))
                if on_result is not None:
                    result, error_text = results[item_id]
                    on_result(item_id, result, error_text)
        return results

    @staticmethod
    def apply_recognition_results(
        items: MutableMapping[str, EducationItem],
        results: Mapping[str, tuple[Any | None, str]],
    ) -> tuple[str, ...]:
        """Apply recognition results and return the IDs that still exist."""
        updated: list[str] = []
        for item_id, (result, error_text) in results.items():
            item = items.get(item_id)
            if item is None:
                continue
            updated.append(item_id)
            item.pop("screenshot_filename", None)
            item.pop("screenshot_directory", None)
            item.update(screenshot_status="", screenshot_detail="", screenshot_path="")
            critical_conflicts = tuple(
                getattr(result, "critical_conflicts", ()) or ()
                if result is not None
                else ()
            )
            if result is not None and result.confidence > 0 and (
                result.name or result.certificate_number or critical_conflicts
            ):
                requires_manual_confirmation = (
                    not result.name or not result.certificate_number
                    or bool(critical_conflicts)
                    or getattr(result, "certificate_type", "education") not in {"education", "degree"}
                )
                item.update({
                    "name": result.name,
                    "certificate_number": result.certificate_number,
                    "certificate_type": getattr(result, "certificate_type", "education"),
                    "school": result.school,
                    "major": result.major,
                    "auto_rotation": result.rotation,
                    "status": (
                        "待人工确认"
                        if requires_manual_confirmation
                        else "已识别"
                    ),
                    "detail": (
                        "证书类型、姓名或证书编号尚未可靠确认，"
                        "请对照证书核对或填写后再验证"
                        if requires_manual_confirmation
                        else (
                            f"识别完成 · 置信度 {result.confidence}% · "
                            f"{result.model}"
                        )
                    ),
                    "warnings": "；".join(result.warnings),
                    "critical_conflicts": critical_conflicts,
                    "manually_edited": bool(item.get("manual_fields")),
                })
                corrections = item.get("manual_fields") or {}
                differences = []
                for field, value in corrections.items():
                    if field not in {"name", "certificate_number", "certificate_type"}:
                        continue
                    if item.get(field) != value:
                        differences.append(field)
                    item[field] = value
                if differences:
                    item["critical_conflicts"] = tuple(dict.fromkeys((*critical_conflicts, *differences)))
                    item["status"] = "待人工确认"
                    item["detail"] = "新识别结果与人工修正不一致，已保留人工值，请核对"
                    field_labels = {"name": "姓名", "certificate_number": "证书编号", "certificate_type": "证书类型"}
                    type_labels = {"education": "学历证书", "degree": "学位证书", "unknown": "待确认"}
                    for field in differences:
                        detected = getattr(result, field) or "空白"
                        retained = corrections[field]
                        if field == "certificate_type":
                            detected = type_labels.get(detected, "待确认")
                            retained = type_labels.get(retained, "待确认")
                        item["warnings"] += f"；{field_labels[field]}：新识别为{detected}，保留人工值{retained}"
                elif corrections and EducationController.fields_ready(item):
                    item["status"] = "信息已修改"
                continue
            item["status"] = "识别失败"
            item["detail"] = "识别失败"
            if result is None:
                item["warnings"] = error_text
            else:
                warnings = "；".join(result.warnings)
                item["warnings"] = warnings or (
                    f"置信度 {result.confidence}%，未识别出姓名或证书编号"
                )
        return tuple(updated)

    @staticmethod
    def prepare_chsi(
        items: MutableMapping[str, EducationItem],
        item_ids: Sequence[str],
        *,
        validator: Callable[[str, str], tuple[str, str]],
    ) -> ChsiPreparation:
        prepared: list[tuple[str, str, str]] = []
        invalid: list[str] = []
        for item_id in item_ids:
            item = items.get(item_id)
            if item is None:
                continue
            try:
                if item.get("critical_conflicts") or item.get("status") == "待人工确认":
                    raise ValueError("请先核对姓名、证书编号及证书类型，再点击关键信息已核对")
                if item.get("certificate_type", "education") not in {"education", "degree"}:
                    raise ValueError("请先确认证书类型：学历证书或学位证书")
                name, certificate_number = validator(
                    str(item.get("name") or ""),
                    str(item.get("certificate_number") or ""),
                )
            except ValueError as exc:
                item.update({
                    "status": "待人工确认" if item.get("critical_conflicts") or item.get("status") == "待人工确认" else "校验失败",
                    "detail": str(exc),
                    "warnings": "",
                })
                invalid.append(item_id)
                continue
            prepared.append((item_id, name, certificate_number))
        return ChsiPreparation(tuple(prepared), tuple(invalid))

    @staticmethod
    def capture_result_screenshots(
        items: Mapping[str, Mapping[str, Any]],
        item_ids: Sequence[str],
        pages: Mapping[str, Any],
        output_dir: str | Path,
        *,
        filename_builder: Callable[..., str],
        existing_validator: Callable[[Path], bool],
        page_alive: Callable[[Any], bool],
        capture: Callable[[Any, str], bytes],
        save: Callable[[bytes, Path], Path],
        is_not_ready_error: Callable[[Exception], bool],
        on_progress: Callable[[ScreenshotItemResult], None] | None = None,
        replace: Callable[[bytes, Path], Path] | None = None,
        validate_page: Callable[[Any, Mapping[str, Any]], bool] | None = None,
    ) -> ScreenshotBatchResult:
        """Capture results; refresh tracked files only via the injected atomic writer."""
        folder = Path(output_dir)
        if not folder.is_dir():
            raise ValueError("截图保存目录不存在")
        emit = on_progress or (lambda _result: None)
        outcomes: list[ScreenshotItemResult] = []

        def finish(result: ScreenshotItemResult) -> None:
            outcomes.append(result)
            emit(result)

        for item_id in item_ids:
            item = items.get(item_id)
            if item is None:
                continue
            name = str(item.get("name") or "").strip()
            certificate_number = str(
                item.get("certificate_number") or ""
            ).strip()
            kind = item.get("certificate_type", "education")
            if kind not in {"education", "degree"}:
                finish(ScreenshotItemResult(item_id, "待结果页", "请先确认证书类型"))
                continue
            filename = str(item.get("screenshot_filename") or "")
            if not filename:
                filename = (
                    filename_builder(name, certificate_number, certificate_type=kind)
                    if kind == "degree" else filename_builder(name, certificate_number)
                )
            if Path(filename).name != filename:
                finish(ScreenshotItemResult(item_id, "文件异常", "截图文件名无效"))
                continue
            target = folder / filename
            replacing = replace is not None and target.exists()
            if replacing and (
                not item.get("screenshot_filename")
                or item.get("screenshot_directory") != str(folder.resolve())
                or not target.is_file()
            ):
                finish(ScreenshotItemResult(item_id, "文件异常", "未找到本次记录对应的原截图，未执行替换"))
                continue
            if target.exists() and not replacing:
                if existing_validator(target):
                    finish(ScreenshotItemResult(
                        item_id,
                        "已存在",
                        "同一规格截图已存在，本次自动跳过",
                        str(target.resolve()),
                    ))
                else:
                    finish(ScreenshotItemResult(
                        item_id,
                        "文件异常",
                        "同名文件存在但不是有效截图，已保留且未覆盖",
                        str(target.resolve()),
                    ))
                continue

            page = pages.get(item_id)
            if page is None:
                finish(ScreenshotItemResult(
                    item_id,
                    "未打开",
                    "尚未创建学信网页面，请先打开学信网验证",
                ))
                continue
            if not page_alive(page):
                finish(ScreenshotItemResult(
                    item_id,
                    "页面已关闭",
                    "对应学信网标签页已关闭或断开",
                ))
                continue

            if not EducationController.result_page_matches_type(page, item):
                finish(ScreenshotItemResult(item_id, "待结果页", "结果页与证书类型不一致，请重新查询"))
                continue

            emit(ScreenshotItemResult(
                item_id,
                "截图中",
                "正在确认结果页并截取内容",
            ))
            try:
                if validate_page is not None and not validate_page(page, item):
                    finish(ScreenshotItemResult(item_id, "待结果页", "结果页姓名或编号不匹配，请重新查询"))
                    continue
                raw_png = capture(page, name)
            except Exception as error:
                error_text = str(error).splitlines()[0][:300] or type(error).__name__
                if replacing:
                    error_text += "；原截图已保留"
                if is_not_ready_error(error):
                    finish(ScreenshotItemResult(
                        item_id,
                        "待结果页",
                        error_text,
                    ))
                else:
                    finish(ScreenshotItemResult(
                        item_id,
                        "截图失败",
                        error_text,
                    ))
                continue
            try:
                writer = replace if replacing else save
                saved_path = writer(raw_png, target)
            except Exception as error:
                error_text = str(error).splitlines()[0][:300] or type(error).__name__
                if replacing:
                    error_text += "；原截图已保留"
                finish(ScreenshotItemResult(
                    item_id,
                    "截图失败",
                    error_text,
                ))
                continue
            finish(ScreenshotItemResult(
                item_id,
                "已保存",
                "已重新截图并替换原文件" if replacing else "结果页截图已按统一规格保存",
                str(saved_path.resolve()),
            ))
        return ScreenshotBatchResult(tuple(outcomes))

    @staticmethod
    def assign_open_result_pages(
        items: Mapping[str, Mapping[str, Any]],
        item_ids: Sequence[str],
        pages: Sequence[Any],
        existing_pages: Mapping[str, Any],
        *,
        page_alive: Callable[[Any], bool],
        read_text: Callable[[Any], str],
        is_result_text: Callable[[str, str], bool],
    ) -> dict[str, Any]:
        """Safely match already-open final CHSI tabs to queued candidates.

        A full certificate number or its final six digits disambiguates people
        with the same name. Name-only matches are accepted only when that name
        appears once in the requested queue and exactly one tab matches.
        """
        assignments: dict[str, Any] = {}
        for item_id, page in existing_pages.items():
            if item_id not in items or not page_alive(page):
                continue
            item = items[item_id]
            name = str(item.get("name") or "").strip()
            try:
                text = re.sub(r"\s+", "", read_text(page))
            except Exception:
                continue
            if (
                text and EducationController.result_page_matches_type(page, item)
                and is_result_text(text, name)
                and EducationController.result_number_matches(text, item, bound=True)
            ):
                assignments[item_id] = page
        used_page_ids = {
            EducationController.page_identity(page)
            for page in assignments.values()
        }
        page_texts: list[tuple[Any, str]] = []
        for page in pages:
            page_id = EducationController.page_identity(page)
            if page_id in used_page_ids or not page_alive(page):
                continue
            try:
                text = re.sub(r"\s+", "", read_text(page))
            except Exception:
                continue
            if text:
                page_texts.append((page, text))

        normalized_names = [
            re.sub(r"\s+", "", str(items[item_id].get("name") or ""))
            for item_id in item_ids
            if item_id in items and item_id not in assignments
        ]
        name_counts = {
            name: normalized_names.count(name)
            for name in set(normalized_names)
            if name
        }

        for item_id in item_ids:
            if item_id not in items or item_id in assignments:
                continue
            item = items[item_id]
            name = str(item.get("name") or "").strip()
            normalized_name = re.sub(r"\s+", "", name)
            certificate_number = re.sub(
                r"\s+", "", str(item.get("certificate_number") or "")
            )
            matches: list[tuple[int, Any]] = []
            for page, text in page_texts:
                if (
                    EducationController.page_identity(page) in used_page_ids
                    or not EducationController.result_page_matches_type(page, item)
                    or not is_result_text(text, name)
                    or not EducationController.result_number_matches(text, item, bound=False)
                ):
                    continue
                score = 1
                if certificate_number and certificate_number in text:
                    score = 3
                elif len(certificate_number) >= 6 and certificate_number[-6:] in text:
                    score = 2
                matches.append((score, page))
            if not matches:
                continue
            best_score = max(score for score, _page in matches)
            best_pages = [page for score, page in matches if score == best_score]
            if len(best_pages) != 1:
                continue
            if best_score == 1 and name_counts.get(normalized_name, 0) != 1:
                continue
            page = best_pages[0]
            assignments[item_id] = page
            used_page_ids.add(EducationController.page_identity(page))
        return assignments

    @staticmethod
    def result_page_matches_type(page: Any, item: Mapping[str, Any]) -> bool:
        """Do not associate the same person's degree tab with a diploma."""
        from urllib.parse import urlparse

        kind = item.get("certificate_type", "education")
        if kind not in {"education", "degree"}:
            return False
        try:
            url = getattr(page, "url", "")
        except Exception:
            return False
        if not isinstance(url, str) or not url:
            return kind == "education"
        try:
            parsed = urlparse(url)
        except ValueError:
            return False
        expected = "/xwcx/" if kind == "degree" else "/xlcx/"
        return parsed.hostname == "www.chsi.com.cn" and parsed.path.startswith(expected)

    @staticmethod
    def page_identity(page: Any) -> tuple[str, Any]:
        """Return a stable browser-tab identity across wrapper instances."""
        for attribute in ("tab_id", "target_id"):
            try:
                value = getattr(page, attribute, None)
                if callable(value):
                    value = value()
            except Exception:
                continue
            if value not in (None, ""):
                return attribute, str(value)
        return "object", id(page)

    @staticmethod
    def fill_and_solve_captcha(
        page: Any,
        name: str,
        certificate_number: str,
        *,
        navigate: Callable[[Any], Any],
        fill_query: Callable[..., Any],
        attempt: Callable[..., tuple[bool, str]],
        browser_lock: Any,
        on_progress: Callable[[str, str], None] | None = None,
        max_attempts: int = EDUCATION_CAPTCHA_MAX_ATTEMPTS,
        navigation_slots: Any = None,
        page_alive: Callable[[Any], bool] | None = None,
        sleep: Callable[[float], None],
    ) -> CaptchaResult:
        emit = on_progress or (lambda *_: None)
        attempts = max(1, int(max_attempts))
        last_status = "待人工验证"

        def page_unavailable() -> bool:
            if page_alive is None:
                return False
            try:
                with browser_lock:
                    return not page_alive(page)
            except Exception:
                return True

        for attempt_no in range(1, attempts + 1):
            retry_delay = 0.0
            if page_unavailable():
                emit("打开失败", "学信网页面已关闭或连接中断")
                return CaptchaResult(False, "打开失败")
            try:
                if attempt_no > 1:
                    emit(
                        f"正在重试验证码（{attempt_no}/{attempts}）...",
                        "正在获取新的验证码",
                    )
                # Each task owns its tab. Network waits must not block other
                # tabs' creation, form updates, or result monitoring.
                with navigation_slots if navigation_slots is not None else browser_lock:
                    navigate(page)
                with browser_lock:
                    emit("正在填写表单...", "正在填写姓名和证书编号")
                    fill_query(
                        page,
                        name,
                        certificate_number,
                        skip_navigation=True,
                    )
                successful, last_status = attempt(
                    page,
                    on_progress=on_progress,
                )
                if page_unavailable():
                    emit("打开失败", "学信网页面已关闭或连接中断")
                    return CaptchaResult(False, "打开失败")
                if successful:
                    return CaptchaResult(True, last_status)
                if last_status == "结果未确认":
                    return CaptchaResult(False, last_status)
            except Exception as error:
                if page_unavailable():
                    emit("打开失败", "学信网页面已关闭或连接中断")
                    return CaptchaResult(False, "打开失败")
                last_status = "待人工验证"
                retry_delay = 1.0
                error_text = str(error).splitlines()[0][:160] or type(error).__name__
                emit("正在重试验证码...", f"本次处理异常：{error_text}")
            if attempt_no < attempts and retry_delay > 0:
                sleep(retry_delay)
        return CaptchaResult(False, last_status)

    @staticmethod
    def attempt_captcha(
        page: Any,
        *,
        config: Mapping[str, Any],
        api_key: str,
        browser_lock: Any,
        min_confidence: int,
        capture_image: Callable[[Any], Any],
        recognize: Callable[
            ...,
            tuple[str, str, int]
            | tuple[str, str, int, str]
            | tuple[str, str, int, str, bool],
        ],
        fill_answer: Callable[[Any, str], bool],
        click_query: Callable[[Any], Any],
        check_result: Callable[[Any], tuple[bool | None, str]],
        resolve_vision_config: Callable[[Mapping[str, Any]], Mapping[str, Any]],
        on_progress: Callable[[str, str], None] | None = None,
        sleep: Callable[[float], None],
    ) -> CaptchaResult:
        emit = on_progress or (lambda *_: None)
        try:
            emit("正在识别验证码...", "正在截取验证码图片")
            with browser_lock:
                vision_config = dict(resolve_vision_config(config))
                data_url = capture_image(page)
        except Exception as error:
            error_text = str(error).splitlines()[0][:160] or type(error).__name__
            emit("正在重试验证码...", f"验证码截图失败：{error_text}")
            return CaptchaResult(False, "待人工验证")

        emit("正在识别验证码...", "AI 模型识别中")
        try:
            recognized = recognize(
                data_url,
                vision_config,
                api_key,
            )
            captcha_type, answer, confidence = recognized[:3]
            diagnostic = str(recognized[3]) if len(recognized) > 3 else ""
            independently_agreed = bool(recognized[4]) if len(recognized) > 4 else False
        except Exception as error:
            error_text = str(error).splitlines()[0][:160] or type(error).__name__
            emit("正在重试验证码...", f"模型识别失败：{error_text}")
            return CaptchaResult(False, "待人工验证")
        if captcha_type == "unknown" or not answer:
            emit(
                "正在重试验证码...",
                diagnostic or "模型未返回可提交的验证码",
            )
            return CaptchaResult(False, "待人工验证")
        if confidence < min_confidence and not independently_agreed:
            emit(
                "正在重试验证码...",
                f"{diagnostic or '模型已返回结果'}，低于提交阈值 {min_confidence}",
            )
            return CaptchaResult(False, "待人工验证")

        emit(
            "正在提交查询...",
            (
                f"{diagnostic}，两路一致，正在填入并提交"
                if independently_agreed
                else diagnostic or f"模型识别置信度 {confidence}，正在提交"
            ),
        )
        try:
            with browser_lock:
                if not fill_answer(page, answer):
                    emit("正在重试验证码...", "验证码输入框写入失败")
                    return CaptchaResult(False, "待人工验证")
                sleep(0.5)
                if not click_query(page):
                    emit("正在重试验证码...", "未找到可用的学信网查询按钮")
                    return CaptchaResult(False, "待人工验证")
            emit("已提交查询", "正在等待页面响应...")
            successful, message = check_result(page)
            if successful is True:
                return CaptchaResult(True, "已提交查询")
            if successful is False:
                emit(
                    "正在重试验证码...",
                    f"{diagnostic or '模型结果已提交'}；网站判定验证码错误：{message}",
                )
                return CaptchaResult(False, "识别失败")
            emit("结果未确认", message or "网站暂未返回明确结果")
            return CaptchaResult(False, "结果未确认")
        except Exception as error:
            error_text = str(error).splitlines()[0][:160] or type(error).__name__
            emit("正在重试验证码...", f"提交或结果检查失败：{error_text}")
            return CaptchaResult(False, "待人工验证")
