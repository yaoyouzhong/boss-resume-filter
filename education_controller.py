"""Education certificate and CHSI workflow control without Tk dependencies."""
from __future__ import annotations

from collections.abc import Callable, Mapping, MutableMapping, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
import re
from typing import Any


EducationItem = dict[str, Any]
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
        recognition_pending = statuses.count("待识别")
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
            return "待结果", "请使用手机扫码确认，随后等待最终学历查询结果"
        if primary_status == EDUCATION_QR_EXPIRED_STATUS:
            return "待结果", "扫码二维码已过期，请刷新二维码后继续扫码"
        if primary_status == "结果未确认":
            return "待结果", "验证码已提交，正在监测二维码或最终查询结果"
        if primary_status == EDUCATION_RESULT_READY_STATUS:
            return "待截图", "已检测到最终学历查询结果，可执行批量截图"
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
        external_result_pending = bool(
            statuses & EDUCATION_VERIFICATION_PENDING_STATUSES
        )
        verification_started = verification_active or bool(
            statuses & EDUCATION_VERIFICATION_STARTED_STATUSES
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
        verification_candidates = [
            item
            for item in items.values()
            if item.get("status") != EDUCATION_RESULT_READY_STATUS
        ]
        verification_fields_ready = bool(verification_candidates) and all(
            str(item.get("name") or "").strip()
            and str(item.get("certificate_number") or "").strip()
            for item in verification_candidates
        )
        retry_ids = EducationController.captcha_retry_item_ids(items)
        return EducationActionStates(
            recognize=(has_items and not busy and not verification_started),
            verify=(
                has_items
                and verification_fields_ready
                and not busy
                and not external_result_pending
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
        max_checks: int = 900,
        interval_seconds: float = 2.0,
        max_unavailable_checks: int = 15,
    ) -> bool:
        """Poll a CHSI tab, tolerating transient unreadability during navigation."""
        checks = max(1, int(max_checks))
        unavailable_limit = max(1, int(max_unavailable_checks))
        unavailable_checks = 0
        for check_no in range(checks):
            try:
                alive = page_alive(page)
            except Exception:
                alive = False
            if not alive:
                unavailable_checks += 1
                if unavailable_checks >= unavailable_limit:
                    return False
            else:
                unavailable_checks = 0
                try:
                    if is_result_text(read_text(page), expected_name):
                        return True
                except Exception:
                    pass
            if check_no + 1 < checks:
                sleep(max(0.0, float(interval_seconds)))
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
    ) -> dict[str, tuple[Any | None, str]]:
        """Recognize concurrently and emit each plain result as it completes."""
        selected = {
            item_id: dict(items[item_id])
            for item_id in item_ids
            if item_id in items
        }
        results: dict[str, tuple[Any | None, str]] = {}

        def recognize_one(item_id: str, item: Mapping[str, Any]) -> Any:
            path = item["path"]
            if item.get("is_pdf"):
                return recognize_pdf(path, dict(config), api_key)
            image_kwargs: dict[str, Any] = {}
            if on_stage is not None:
                image_kwargs["on_progress"] = lambda stage, percent: on_stage(
                    item_id, stage, percent
                )
            if item.get("recognition_rotation") in (0, 90, 180, 270):
                image_kwargs["rotation_override"] = item[
                    "recognition_rotation"
                ]
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
            critical_conflicts = tuple(
                getattr(result, "critical_conflicts", ()) or ()
                if result is not None
                else ()
            )
            if result is not None and result.confidence > 0 and (
                result.name or result.certificate_number or critical_conflicts
            ):
                requires_manual_confirmation = bool(critical_conflicts)
                item.update({
                    "name": result.name,
                    "certificate_number": result.certificate_number,
                    "school": result.school,
                    "major": result.major,
                    "auto_rotation": result.rotation,
                    "status": (
                        "待人工确认"
                        if requires_manual_confirmation
                        else "已识别"
                    ),
                    "detail": (
                        "姓名或证书编号尚未可靠确认，"
                        "请对照证书核对或填写后再验证"
                        if requires_manual_confirmation
                        else (
                            f"识别完成 · 置信度 {result.confidence}% · "
                            f"{result.model}"
                        )
                    ),
                    "warnings": "；".join(result.warnings),
                    "manually_edited": False,
                })
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
                name, certificate_number = validator(
                    str(item.get("name") or ""),
                    str(item.get("certificate_number") or ""),
                )
            except ValueError as exc:
                item.update({
                    "status": "校验失败",
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
        filename_builder: Callable[[str, str], str],
        existing_validator: Callable[[Path], bool],
        page_alive: Callable[[Any], bool],
        capture: Callable[[Any, str], bytes],
        save: Callable[[bytes, Path], Path],
        is_not_ready_error: Callable[[Exception], bool],
        on_progress: Callable[[ScreenshotItemResult], None] | None = None,
    ) -> ScreenshotBatchResult:
        """Capture missing final-result tabs sequentially and never overwrite files."""
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
            target = folder / filename_builder(name, certificate_number)
            if target.exists():
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

            emit(ScreenshotItemResult(
                item_id,
                "截图中",
                "正在确认结果页并截取内容",
            ))
            try:
                raw_png = capture(page, name)
            except Exception as error:
                error_text = str(error).splitlines()[0][:300] or type(error).__name__
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
                saved_path = save(raw_png, target)
            except Exception as error:
                error_text = str(error).splitlines()[0][:300] or type(error).__name__
                finish(ScreenshotItemResult(
                    item_id,
                    "截图失败",
                    error_text,
                ))
                continue
            finish(ScreenshotItemResult(
                item_id,
                "已保存",
                "结果页截图已按统一规格保存",
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
            if text and is_result_text(text, name):
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
                    or not is_result_text(text, name)
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
                navigate(page)
                emit("正在填写表单...", "正在填写姓名和证书编号")
                with browser_lock:
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
