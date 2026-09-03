from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
import tempfile

from education_controller import (
    EDUCATION_FORM_EMPTY_STATUS,
    EDUCATION_RESULT_NOT_FOUND_STATUS,
    EDUCATION_RESULT_READY_STATUS,
    EDUCATION_WAITING_FOR_SCAN_STATUS,
    EducationController,
)


def test_import_batch_validates_deduplicates_and_numbers_items():
    temp_dir = tempfile.TemporaryDirectory()
    root = Path(temp_dir.name)
    existing = root / "existing.pdf"
    existing.touch()
    image = root / "new.png"
    image.touch()

    def validator(raw):
        if raw == "invalid.exe":
            raise ValueError("unsupported")
        return Path(raw)

    try:
        batch = EducationController.prepare_import(
            (str(existing), str(image), "invalid.exe"),
            {"education_1": {"path": str(existing)}},
            1,
            validator=validator,
            is_pdf=lambda path: path.suffix == ".pdf",
        )
    finally:
        temp_dir.cleanup()

    assert list(batch.items) == ["education_2"]
    assert batch.items["education_2"]["is_pdf"] is False
    assert batch.items["education_2"]["screenshot_status"] == "待识别"
    assert batch.invalid_files == ("invalid.exe",)


def test_queue_status_summary_separates_recognition_from_chsi_stages():
    items = {
        "recognized": {"status": "已识别"},
        "waiting_scan": {"status": EDUCATION_WAITING_FOR_SCAN_STATUS},
        "waiting_result": {"status": "结果未确认"},
        "ready": {"status": EDUCATION_RESULT_READY_STATUS},
        "not_found": {"status": EDUCATION_RESULT_NOT_FOUND_STATUS},
        "captcha": {"status": "验证码识别失败"},
        "failed": {"status": EDUCATION_FORM_EMPTY_STATUS},
    }

    summary = EducationController.summarize_queue_statuses(items)

    assert summary.total == 7
    assert summary.recognized == 7
    assert summary.verification_not_started == 1
    assert summary.waiting_scan == 1
    assert summary.waiting_result == 1
    assert summary.result_ready == 1
    assert summary.result_not_found == 1
    assert summary.verification_attention == 1
    assert summary.verification_failed == 1


def test_queue_status_summary_counts_completed_manual_edit_as_chsi_ready():
    items = {
        "manual": {
            "status": "信息已修改",
            "name": "鲍殊",
            "certificate_number": "102891202305002814",
        },
        **{
            f"recognized_{index}": {
                "status": "已识别",
                "name": f"候选人{index}",
                "certificate_number": str(index).zfill(18),
            }
            for index in range(4)
        },
    }

    summary = EducationController.summarize_queue_statuses(items)

    assert summary.recognized == 4
    assert summary.manually_completed == 1
    assert summary.information_ready == 5
    assert summary.manual_review == 0
    assert summary.verification_not_started == 5


def test_screenshot_readiness_does_not_claim_failed_items_are_ready():
    assert EducationController.screenshot_readiness({
        "status": EDUCATION_WAITING_FOR_SCAN_STATUS,
        "name": "张三",
        "certificate_number": "123456789012345678",
    })[0] == "待结果"
    assert EducationController.screenshot_readiness({
        "status": "识别失败",
        "name": "",
        "certificate_number": "",
    })[0] == "待识别"
    assert EducationController.screenshot_readiness({
        "status": "识别失败",
        "name": "张三",
        "certificate_number": "123456789012345678",
    })[0] == "待验证"
    assert EducationController.screenshot_readiness({
        "status": "已提交查询",
        "name": "张三",
        "certificate_number": "123456789012345678",
    })[0] == "待结果"
    assert EducationController.screenshot_readiness({
        "status": "结果未确认",
        "name": "张三",
        "certificate_number": "123456789012345678",
    })[0] == "待结果"
    assert EducationController.screenshot_readiness({
        "status": EDUCATION_RESULT_READY_STATUS,
        "name": "张三",
        "certificate_number": "123456789012345678",
    })[0] == "待截图"
    assert EducationController.screenshot_readiness({
        "status": EDUCATION_RESULT_NOT_FOUND_STATUS,
        "name": "张三",
        "certificate_number": "123456789012345678",
    })[0] == "无需截图"


def test_result_and_captcha_retry_scopes_use_explicit_queue_statuses():
    items = {
        "ready": {"status": EDUCATION_RESULT_READY_STATUS},
        "not_found": {"status": EDUCATION_RESULT_NOT_FOUND_STATUS},
        "manual": {"status": "待人工验证"},
        "captcha": {"status": "验证码识别失败"},
        "certificate": {"status": "识别失败"},
        "browser": {"status": "打开失败"},
        "submitted": {"status": "已提交查询"},
        "waiting_scan": {"status": EDUCATION_WAITING_FOR_SCAN_STATUS},
        "unknown": {"status": "结果未确认"},
    }

    assert EducationController.result_ready_item_ids(items) == ("ready",)
    assert EducationController.captcha_retry_item_ids(items) == (
        "manual",
        "captcha",
    )


def test_education_actions_coordinate_normal_and_running_states():
    initial = EducationController.action_states({
        "item": {
            "status": "已识别",
            "name": "张三",
            "certificate_number": "123456789012345678",
        },
    })
    assert initial.recognize is True
    assert initial.verify is True
    assert initial.screenshot is False
    assert initial.retry_captcha is False

    incomplete = EducationController.action_states({
        "item": {"status": "待识别", "name": "", "certificate_number": ""},
    })
    assert incomplete.recognize is True
    assert incomplete.verify is False

    recognizing = EducationController.action_states(
        {"item": {"status": "识别中"}},
        recognition_running=True,
    )
    assert recognizing == type(recognizing)(False, False, False, False)

    verifying = EducationController.action_states({
        "item": {"status": "正在识别验证码..."},
    })
    assert verifying == type(verifying)(False, False, False, False)

    screenshotting = EducationController.action_states(
        {"item": {"status": EDUCATION_RESULT_READY_STATUS}},
        screenshot_running=True,
    )
    assert screenshotting == type(screenshotting)(False, False, False, False)


def test_education_actions_lock_submitted_data_but_recover_after_browser_failure():
    submitted = EducationController.action_states({
        "waiting": {"status": "已提交查询"},
        "ready": {"status": EDUCATION_RESULT_READY_STATUS},
    })
    assert submitted.recognize is False
    assert submitted.verify is False
    assert submitted.screenshot is True
    assert submitted.retry_captcha is False

    waiting_scan = EducationController.action_states({
        "waiting": {"status": EDUCATION_WAITING_FOR_SCAN_STATUS},
    })
    assert waiting_scan == type(waiting_scan)(False, False, True, False)

    manual = EducationController.action_states({
        "item": {"status": "验证码识别失败"},
    })
    assert manual.recognize is False
    assert manual.verify is False
    assert manual.screenshot is False
    assert manual.retry_captcha is True

    interrupted = EducationController.action_states({
        "item": {
            "status": "打开失败",
            "name": "张三",
            "certificate_number": "123456789012345678",
        },
    })
    assert interrupted.recognize is True
    assert interrupted.verify is True
    assert interrupted.screenshot is False
    assert interrupted.retry_captcha is False

    partial = EducationController.action_states({
        "ready": {"status": EDUCATION_RESULT_READY_STATUS},
        "interrupted": {
            "status": "打开失败",
            "name": "李四",
            "certificate_number": "987654321098765432",
        },
    })
    assert partial.recognize is False
    assert partial.verify is True
    assert partial.screenshot is True


def test_result_page_watcher_checks_existing_page_until_result_appears():
    reads = iter(("等待手机确认", "姓名 张三 性别 男 学校名称 甲大学 专业 计算机"))
    sleeps = []

    ready = EducationController.wait_for_result_page(
        object(),
        "张三",
        page_alive=lambda _page: True,
        read_text=lambda _page: next(reads),
        is_result_text=lambda text, name: name in text and "学校名称" in text,
        sleep=sleeps.append,
        max_checks=2,
        interval_seconds=0.25,
    )

    assert ready is True
    assert sleeps == [0.25]


def test_result_page_watcher_tolerates_transient_page_unavailability():
    availability = iter((False, False, True))
    sleeps = []

    ready = EducationController.wait_for_result_page(
        object(),
        "张三",
        page_alive=lambda _page: next(availability),
        read_text=lambda _page: "学校名称 甲大学",
        is_result_text=lambda text, _name: "学校名称" in text,
        sleep=sleeps.append,
        max_checks=3,
        interval_seconds=0.25,
        max_unavailable_checks=3,
    )

    assert ready is True
    assert sleeps == [0.25, 0.25]


def test_education_model_ref_overrides_default_only_when_complete():
    config = {
        "model": "default",
        "education_model_ref": {"model": "vision", "api_provider": "qwen"},
    }
    assert EducationController.resolve_api_config(config)["model"] == "vision"
    assert EducationController.resolve_api_config({"model": "default"})["model"] == "default"


def test_recognition_batch_and_apply_keep_failures_explicit():
    items = {
        "image": {
            "path": "a.png",
            "is_pdf": False,
            "recognition_rotation": 90,
        },
        "pdf": {"path": "b.pdf", "is_pdf": True},
    }
    success = SimpleNamespace(
        confidence=91,
        name="张三",
        certificate_number="123",
        school="大学",
        major="计算机",
        rotation=90,
        model="vision",
        warnings=[],
    )
    emitted = []
    stages = []

    def recognize_image(*_args, on_progress=None, rotation_override=None):
        assert rotation_override == 90
        if on_progress is not None:
            on_progress("正在核对姓名和证书编号", 70)
        return success

    results = EducationController.recognize_documents(
        items,
        ("image", "pdf"),
        {"model": "vision"},
        "secret",
        recognize_image=recognize_image,
        recognize_pdf=lambda *_args: (_ for _ in ()).throw(RuntimeError("bad pdf")),
        on_result=lambda item_id, result, error: emitted.append(
            (item_id, result, error)
        ),
        on_stage=lambda item_id, stage, percent: stages.append(
            (item_id, stage, percent)
        ),
    )
    updated = EducationController.apply_recognition_results(items, results)

    assert set(updated) == {"image", "pdf"}
    assert items["image"]["status"] == "已识别"
    assert items["pdf"]["status"] == "识别失败"
    assert items["pdf"]["warnings"] == "bad pdf"
    assert {item_id for item_id, _result, _error in emitted} == {"image", "pdf"}
    assert next(error for item_id, _result, error in emitted if item_id == "pdf") == "bad pdf"
    assert stages == [("image", "正在核对姓名和证书编号", 70)]


def test_recognition_conflict_requires_manual_name_and_number_entry():
    items = {"image": {"path": "a.png", "is_pdf": False}}
    conflict = SimpleNamespace(
        confidence=96,
        name="",
        certificate_number="",
        school="某大学",
        major="计算机",
        rotation=0,
        model="MiniMax-M3",
        warnings=(
            "姓名两次识别结果不一致，已留空，请对照证书人工填写",
            "证书编号两次识别结果不一致，已留空，请对照证书人工填写",
        ),
        critical_conflicts=("name", "certificate_number"),
    )

    EducationController.apply_recognition_results(
        items,
        {"image": (conflict, "")},
    )

    assert items["image"]["status"] == "待人工确认"
    assert "填写后再验证" in items["image"]["detail"]
    assert EducationController.action_states(items).verify is False


def test_chsi_preparation_marks_invalid_items_and_returns_valid_requests():
    items = {
        "ok": {"name": "张三", "certificate_number": "123"},
        "bad": {"name": "", "certificate_number": ""},
    }

    def validate(name, number):
        if not name:
            raise ValueError("姓名缺失")
        return name, number

    result = EducationController.prepare_chsi(
        items,
        ("ok", "bad"),
        validator=validate,
    )

    assert result.prepared == (("ok", "张三", "123"),)
    assert result.invalid_ids == ("bad",)
    assert items["bad"]["status"] == "校验失败"


def test_captcha_attempt_submits_only_confident_answers():
    events = []
    result = EducationController.attempt_captcha(
        object(),
        config={"model": "vision"},
        api_key="secret",
        browser_lock=nullcontext(),
        min_confidence=80,
        capture_image=lambda _page: "data:image/png;base64,x",
        recognize=lambda *_args: ("text", "ABCD", 95),
        fill_answer=lambda _page, answer: events.append(answer) or True,
        click_query=lambda _page: events.append("clicked") or True,
        check_result=lambda _page: (True, "ok"),
        resolve_vision_config=dict,
        sleep=lambda _seconds: None,
    )

    assert result.successful is True
    assert result.status == "已提交查询"
    assert events == ["ABCD", "clicked"]


def test_captcha_unknown_result_stops_without_refreshing_the_page():
    attempts = []
    result = EducationController.fill_and_solve_captcha(
        object(),
        "张三",
        "123456789012345678",
        navigate=lambda _page: attempts.append("navigate"),
        fill_query=lambda *_args, **_kwargs: attempts.append("fill"),
        attempt=lambda *_args, **_kwargs: (False, "结果未确认"),
        browser_lock=nullcontext(),
        max_attempts=5,
        sleep=lambda _seconds: attempts.append("sleep"),
    )

    assert result.successful is False
    assert result.status == "结果未确认"
    assert attempts == ["navigate", "fill"]


def test_explicit_captcha_errors_retry_immediately_without_fixed_sleep():
    attempts = iter((
        (False, "识别失败"),
        (False, "识别失败"),
        (True, "已提交查询"),
    ))
    sleeps = []

    result = EducationController.fill_and_solve_captcha(
        object(),
        "张三",
        "123456789012345678",
        navigate=lambda _page: None,
        fill_query=lambda *_args, **_kwargs: None,
        attempt=lambda *_args, **_kwargs: next(attempts),
        browser_lock=nullcontext(),
        max_attempts=5,
        sleep=sleeps.append,
    )

    assert result.successful is True
    assert result.status == "已提交查询"
    assert sleeps == []


def test_captcha_flow_stops_immediately_when_browser_page_is_closed():
    events = []
    progress = []

    result = EducationController.fill_and_solve_captcha(
        object(),
        "张三",
        "123456789012345678",
        navigate=lambda _page: events.append("navigate"),
        fill_query=lambda *_args, **_kwargs: events.append("fill"),
        attempt=lambda *_args, **_kwargs: events.append("attempt"),
        browser_lock=nullcontext(),
        page_alive=lambda _page: False,
        on_progress=lambda status, detail: progress.append((status, detail)),
        max_attempts=5,
        sleep=lambda _seconds: events.append("sleep"),
    )

    assert result.successful is False
    assert result.status == "打开失败"
    assert events == []
    assert progress == [("打开失败", "学信网页面已关闭或连接中断")]


def test_captcha_attempt_reports_why_model_result_was_not_submitted():
    progress = []

    result = EducationController.attempt_captcha(
        object(),
        config={"model": "vision"},
        api_key="secret",
        browser_lock=nullcontext(),
        min_confidence=70,
        capture_image=lambda _page: object(),
        recognize=lambda *_args: (
            "letter",
            "ABCD",
            62,
            "模型识别出 4 位字符，置信度 62",
        ),
        fill_answer=lambda _page, _answer: True,
        click_query=lambda _page: True,
        check_result=lambda _page: (True, "ok"),
        resolve_vision_config=dict,
        on_progress=lambda status, detail: progress.append((status, detail)),
        sleep=lambda _seconds: None,
    )

    assert result.successful is False
    assert result.status == "待人工验证"
    assert progress[-1] == (
        "正在重试验证码...",
        "模型识别出 4 位字符，置信度 62，低于提交阈值 70",
    )


def test_captcha_attempt_submits_low_self_confidence_when_two_reads_agree():
    events = []
    progress = []

    result = EducationController.attempt_captcha(
        object(),
        config={"model": "vision"},
        api_key="secret",
        browser_lock=nullcontext(),
        min_confidence=70,
        capture_image=lambda _page: object(),
        recognize=lambda *_args: (
            "letter",
            "ABCD",
            62,
            "原色与去噪图识别结果一致",
            True,
        ),
        fill_answer=lambda _page, answer: events.append(answer) or True,
        click_query=lambda _page: events.append("clicked") or True,
        check_result=lambda _page: (True, "ok"),
        resolve_vision_config=dict,
        on_progress=lambda status, detail: progress.append((status, detail)),
        sleep=lambda _seconds: None,
    )

    assert result.successful is True
    assert result.status == "已提交查询"
    assert events == ["ABCD", "clicked"]
    assert (
        "正在提交查询...",
        "原色与去噪图识别结果一致，两路一致，正在填入并提交",
    ) in progress
    assert progress[-1] == (
        "已提交查询",
        "正在等待页面响应...",
    )


def test_captcha_attempt_keeps_model_diagnostic_when_site_rejects_answer():
    progress = []

    result = EducationController.attempt_captcha(
        object(),
        config={"model": "vision"},
        api_key="secret",
        browser_lock=nullcontext(),
        min_confidence=70,
        capture_image=lambda _page: object(),
        recognize=lambda *_args: (
            "letter",
            "ABCD",
            88,
            "两路识别结果一致（4 位），模型置信度 88",
            True,
        ),
        fill_answer=lambda _page, _answer: True,
        click_query=lambda _page: True,
        check_result=lambda _page: (False, "验证码错误"),
        resolve_vision_config=dict,
        on_progress=lambda status, detail: progress.append((status, detail)),
        sleep=lambda _seconds: None,
    )

    assert result.successful is False
    assert result.status == "识别失败"
    assert progress[-1] == (
        "正在重试验证码...",
        "两路识别结果一致（4 位），模型置信度 88；"
        "网站判定验证码错误：验证码错误",
    )


def test_captcha_attempt_preserves_unknown_query_result_state():
    result = EducationController.attempt_captcha(
        object(),
        config={"model": "vision"},
        api_key="secret",
        browser_lock=nullcontext(),
        min_confidence=80,
        capture_image=lambda _page: object(),
        recognize=lambda *_args: ("letter", "ABCD", 95),
        fill_answer=lambda _page, _answer: True,
        click_query=lambda _page: True,
        check_result=lambda _page: (None, "未检测到明确结果"),
        resolve_vision_config=dict,
        sleep=lambda _seconds: None,
    )

    assert result.successful is False
    assert result.status == "结果未确认"


def test_screenshot_batch_skips_existing_and_keeps_missing_items_retryable():
    class NotReadyError(RuntimeError):
        pass

    items = {
        "existing": {"name": "已有", "certificate_number": "111"},
        "capture": {"name": "新存", "certificate_number": "222"},
        "pending": {"name": "待补", "certificate_number": "333"},
        "unopened": {"name": "未开", "certificate_number": "444"},
        "closed": {"name": "已关", "certificate_number": "555"},
    }
    pages = {
        "existing": object(),
        "capture": "capture-page",
        "pending": "pending-page",
        "closed": "closed-page",
    }
    progress = []
    with tempfile.TemporaryDirectory() as temp_dir:
        folder = Path(temp_dir)
        (folder / "已有.png").write_bytes(b"valid")

        def capture(page, _name):
            if page == "pending-page":
                raise NotReadyError("尚未检测到结果页")
            return b"raw-png"

        def save(raw, path):
            assert raw == b"raw-png"
            path.write_bytes(b"saved")
            return path

        result = EducationController.capture_result_screenshots(
            items,
            tuple(items),
            pages,
            folder,
            filename_builder=lambda name, _number: f"{name}.png",
            existing_validator=lambda path: path.read_bytes() == b"valid",
            page_alive=lambda page: page != "closed-page",
            capture=capture,
            save=save,
            is_not_ready_error=lambda error: isinstance(error, NotReadyError),
            on_progress=progress.append,
        )

        assert (folder / "新存.png").read_bytes() == b"saved"

    statuses = {item.item_id: item.status for item in result.items}
    assert statuses == {
        "existing": "已存在",
        "capture": "已保存",
        "pending": "待结果页",
        "unopened": "未打开",
        "closed": "页面已关闭",
    }
    assert result.saved == 1
    assert result.skipped == 1
    assert result.pending == 2
    assert result.failed == 1
    assert any(item.status == "截图中" for item in progress)


def test_screenshot_batch_preserves_invalid_same_name_file():
    items = {"one": {"name": "张三", "certificate_number": "123"}}
    save_calls = []
    with tempfile.TemporaryDirectory() as temp_dir:
        folder = Path(temp_dir)
        target = folder / "张三.png"
        target.write_bytes(b"user-file")

        result = EducationController.capture_result_screenshots(
            items,
            ("one",),
            {"one": object()},
            folder,
            filename_builder=lambda *_args: "张三.png",
            existing_validator=lambda _path: False,
            page_alive=lambda _page: True,
            capture=lambda *_args: b"raw",
            save=lambda *_args: save_calls.append(True),
            is_not_ready_error=lambda _error: False,
        )

        assert target.read_bytes() == b"user-file"

    assert result.items[0].status == "文件异常"
    assert result.failed == 1
    assert save_calls == []


def test_assign_open_result_pages_recovers_existing_tabs_and_uses_certificate_tail():
    first_page = object()
    second_page = object()
    texts = {
        first_page: "姓名张三性别男出生日期1990学校名称甲大学专业计算机学历层次本科证书编号111111",
        second_page: "姓名张三性别男出生日期1991学校名称乙大学专业金融学历层次本科证书编号222222",
    }
    items = {
        "one": {"name": "张三", "certificate_number": "000000000000111111"},
        "two": {"name": "张三", "certificate_number": "000000000000222222"},
    }

    matched = EducationController.assign_open_result_pages(
        items,
        ("one", "two"),
        (second_page, first_page),
        {},
        page_alive=lambda _page: True,
        read_text=texts.__getitem__,
        is_result_text=lambda text, name: name in text and "学校名称" in text,
    )

    assert matched == {"one": first_page, "two": second_page}


def test_assign_open_result_pages_rejects_ambiguous_same_name_only_match():
    page = object()
    items = {
        "one": {"name": "张三", "certificate_number": "111111"},
        "two": {"name": "张三", "certificate_number": "222222"},
    }

    matched = EducationController.assign_open_result_pages(
        items,
        ("one", "two"),
        (page,),
        {},
        page_alive=lambda _page: True,
        read_text=lambda _page: (
            "姓名张三性别男学校名称某大学专业计算机学历层次本科"
        ),
        is_result_text=lambda text, name: name in text and "学校名称" in text,
    )

    assert matched == {}


def test_assign_open_result_pages_replaces_alive_query_tab_with_actual_result():
    old_query_page = object()
    result_page = object()
    texts = {
        old_query_page: "证书编号 姓名 图片验证码 免费查询",
        result_page: (
            "姓名张三性别男出生日期1990学校名称甲大学"
            "专业计算机学历层次本科证书编号123456"
        ),
    }
    items = {
        "one": {
            "name": "张三",
            "certificate_number": "000000000000123456",
        }
    }

    matched = EducationController.assign_open_result_pages(
        items,
        ("one",),
        (old_query_page, result_page),
        {"one": old_query_page},
        page_alive=lambda _page: True,
        read_text=texts.__getitem__,
        is_result_text=lambda text, name: name in text and "学校名称" in text,
    )

    assert matched == {"one": result_page}


def test_page_identity_uses_tab_id_across_new_wrapper_objects():
    class Page:
        tab_id = "same-target"

    assert EducationController.page_identity(Page()) == (
        "tab_id",
        "same-target",
    )
    assert EducationController.page_identity(Page()) == (
        "tab_id",
        "same-target",
    )
