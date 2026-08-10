"""Education certificate and CHSI workflow control without Tk dependencies."""
from __future__ import annotations

from collections.abc import Callable, Mapping, MutableMapping, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any


EducationItem = dict[str, Any]


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


class EducationController:
    """Coordinate certificate recognition and captcha state as plain data."""

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
            }
        return EducationImportBatch(additions, tuple(invalid_files), counter)

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
    ) -> dict[str, tuple[Any | None, str]]:
        """Recognize documents concurrently and return only plain results."""
        selected = {
            item_id: dict(items[item_id])
            for item_id in item_ids
            if item_id in items
        }
        results: dict[str, tuple[Any | None, str]] = {}

        def recognize_one(item: Mapping[str, Any]) -> Any:
            path = item["path"]
            if item.get("is_pdf"):
                return recognize_pdf(path, dict(config), api_key)
            return recognize_image(path, dict(config), api_key)

        workers = min(max(1, max_workers), max(1, len(selected)))
        try:
            with ThreadPoolExecutor(max_workers=workers) as executor:
                futures = {
                    executor.submit(recognize_one, item): item_id
                    for item_id, item in selected.items()
                }
                for future in as_completed(futures):
                    item_id = futures[future]
                    try:
                        results[item_id] = (future.result(), "")
                    except Exception as exc:
                        results[item_id] = (None, str(exc))
        except Exception as exc:
            error = str(exc)
            for item_id in selected:
                results.setdefault(item_id, (None, error))
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
            if result is not None and result.confidence > 0 and (
                result.name or result.certificate_number
            ):
                item.update({
                    "name": result.name,
                    "certificate_number": result.certificate_number,
                    "school": result.school,
                    "major": result.major,
                    "auto_rotation": result.rotation,
                    "status": "已识别",
                    "detail": (
                        f"识别完成 · 置信度 {result.confidence}% · {result.model}"
                    ),
                    "warnings": "；".join(result.warnings),
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
        max_attempts: int = 3,
        sleep: Callable[[float], None],
    ) -> CaptchaResult:
        emit = on_progress or (lambda *_: None)
        attempts = max(1, int(max_attempts))
        last_status = "待人工验证"
        for attempt_no in range(1, attempts + 1):
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
                if successful:
                    return CaptchaResult(True, last_status)
            except Exception:
                last_status = "待人工验证"
            if attempt_no < attempts:
                sleep(1)
        return CaptchaResult(False, last_status)

    @staticmethod
    def attempt_captcha(
        page: Any,
        *,
        config: Mapping[str, Any],
        api_key: str,
        browser_lock: Any,
        min_confidence: int,
        capture_image: Callable[[Any], str],
        recognize: Callable[..., tuple[str, str, int]],
        fill_answer: Callable[[Any, str], bool],
        click_query: Callable[[Any], Any],
        check_result: Callable[[Any], tuple[bool, str]],
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
        except Exception:
            return CaptchaResult(False, "待人工验证")

        emit("正在识别验证码...", "AI 模型识别中")
        try:
            captcha_type, answer, confidence = recognize(
                data_url,
                vision_config,
                api_key,
            )
        except Exception:
            return CaptchaResult(False, "待人工验证")
        if captcha_type == "unknown" or not answer or confidence < min_confidence:
            return CaptchaResult(False, "待人工验证")

        emit("正在提交查询...", "验证码已识别，正在提交")
        try:
            with browser_lock:
                if not fill_answer(page, answer):
                    return CaptchaResult(False, "待人工验证")
                sleep(0.5)
                click_query(page)
            emit("已提交查询", "正在等待页面响应...")
            successful, _message = check_result(page)
            return CaptchaResult(
                successful,
                "已提交查询" if successful else "识别失败",
            )
        except Exception:
            return CaptchaResult(False, "待人工验证")
