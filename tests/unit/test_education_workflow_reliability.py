"""Certificate identity, incremental submissions and bounded recognition contracts."""
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event, RLock
from types import SimpleNamespace
from unittest.mock import Mock, patch

from education_controller import EducationController as Controller
from education_certificate import (
    RecognitionDeadlineExceeded, RecognitionStopped, _bounded_recognition,
    _remaining_recognition_time, is_chsi_result_text, normalize_recognition,
    recognize_certificate_pdf, validate_chsi_fields,
)


def ready(**changes):
    return dict(name="测试甲", certificate_number="12345678901234567",
                certificate_type="education", status="已识别", **changes)


def test_later_corrected_record_submits_without_resubmitting_first_wave():
    items = {"first": ready(), "later": dict(ready(), status="待人工确认", critical_conflicts=("name",))}
    assert Controller.action_states(items).verify
    assert Controller.verification_item_ids(items) == ("first",)
    items["first"]["status"] = "等待扫码"
    assert not Controller.action_states(items).verify
    assert not Controller.prepare_chsi(items, ["later"], validator=validate_chsi_fields).prepared
    Controller.confirm_fields(items["later"], validate_chsi_fields)
    assert Controller.action_states(items).verify
    ids = Controller.verification_item_ids(items)
    assert ids == ("later",)
    assert Controller.prepare_chsi(items, ids, validator=validate_chsi_fields).prepared == (
        ("later", "测试甲", "12345678901234567"),
    )
    assert items["first"]["status"] == "等待扫码"


def test_missing_and_unknown_fields_never_become_ready():
    for changes in ({"name": ""}, {"certificate_number": ""}, {"certificate_type": "unknown"}):
        payload = dict(ready(), confidence=95, **changes)
        item = {}
        Controller.apply_recognition_results({"a": item}, {"a": (normalize_recognition(payload), "")})
        assert item["status"] == "待人工确认"
        assert not Controller.verification_item_ids({"a": item})


def test_recognition_preserves_manual_values_and_reports_new_disagreement():
    item = ready(manually_edited=True, manual_fields={"name": "人工姓名"})
    item["name"] = "人工姓名"
    result = normalize_recognition(dict(ready(), confidence=95))
    Controller.apply_recognition_results({"a": item}, {"a": (result, "")})
    assert item["name"] == "人工姓名"
    assert item["status"] == "待人工确认"
    assert "测试甲" in item["warnings"] and "人工姓名" in item["warnings"]
    assert not Controller.verification_item_ids({"a": item})


def test_default_recognition_skips_completed_and_manually_corrected_records():
    items = {"done": ready(), "manual": dict(ready(), status="信息已修改", manually_edited=True),
             "new": {"status": "待识别"}, "failed": {"status": "识别失败"}}
    assert Controller.recognition_item_ids(items) == ("new", "failed")
    assert Controller.recognition_item_ids(items, force=True) == tuple(items)


def test_explicit_wrong_number_rejected_even_in_previously_bound_tab():
    page = SimpleNamespace(url="https://www.chsi.com.cn/xlcx/result", tab_id="tab1")
    text = "姓名测试甲性别男出生日期1990学校名称测试大学专业计算机学历层次本科证书编号99999999999999999"
    assert is_chsi_result_text(text, "测试甲")
    for previous in ({}, {"a": page}):
        assert not Controller.assign_open_result_pages(
            {"a": ready()}, ["a"], [page], previous,
            page_alive=lambda _: True, read_text=lambda _: text, is_result_text=is_chsi_result_text,
        )


def test_masked_or_missing_number_requires_bound_query_and_matching_visible_digits():
    assert Controller.result_number_matches("证书编号***********234567", ready(), bound=True)
    assert not Controller.result_number_matches("证书编号***********234567", ready(), bound=False)
    assert not Controller.result_number_matches("证书编号***********999999", ready(), bound=True)
    assert Controller.result_number_matches("姓名测试甲", ready(), bound=True)
    assert not Controller.result_number_matches("姓名测试甲", ready(), bound=False)


def test_screenshot_mismatch_never_captures_or_overwrites_file():
    with TemporaryDirectory() as directory:
        capture = Mock()
        save = Mock()
        result = Controller.capture_result_screenshots(
            {"a": ready()}, ["a"], {"a": SimpleNamespace(url="https://www.chsi.com.cn/xlcx/result")}, directory,
            filename_builder=lambda *_: "result.png", existing_validator=lambda _: True,
            page_alive=lambda _: True, capture=capture, save=save,
            is_not_ready_error=lambda _: False, validate_page=lambda *_: False,
        )
        assert result.items[0].status == "待结果页"
        capture.assert_not_called()
        save.assert_not_called()
        assert list(Path(directory).iterdir()) == []


def test_stop_skips_pending_documents_and_keeps_completed_result():
    stop = Event()
    seen = []
    def recognize(path, *_args, **_kwargs):
        seen.append(path)
        stop.set()
        return "completed"
    items = {str(i): {"path": str(i), "is_pdf": False} for i in range(4)}
    results = Controller.recognize_documents(items, list(items), {}, "", max_workers=1,
        recognize_image=recognize, recognize_pdf=recognize, stop_event=stop)
    assert seen == ["0"]
    assert results["0"] == ("completed", "")
    assert all(results[str(i)] == (None, "识别已停止") for i in range(1, 4))


def test_nested_recognition_uses_one_deadline_and_resets_after_failure():
    calls = []
    @_bounded_recognition
    def inner():
        calls.append(_remaining_recognition_time(120))
    @_bounded_recognition
    def outer():
        inner()
        inner()
    with patch("education_certificate.time.monotonic", side_effect=[0, 0, 2, 2, 3, 11]):
        try:
            outer(total_timeout=10)
        except RecognitionDeadlineExceeded:
            pass
        else:
            raise AssertionError("Nested calls must not reset the document deadline")
    assert calls == [8]
    assert _remaining_recognition_time(120) == 120


def test_stopped_recognition_never_starts_work():
    stop = Event()
    stop.set()
    work = Mock()
    try:
        _bounded_recognition(work)(stop_event=stop)
    except RecognitionStopped:
        pass
    else:
        raise AssertionError("Stop should abort before reading or requesting")
    work.assert_not_called()


def test_pdf_header_text_does_not_bypass_visual_recognition():
    result = normalize_recognition(dict(ready(), confidence=95))
    with patch("education_pdf_images.extract_certificate_pages", return_value=[Path("page.png")]), \
         patch("education_certificate.recognize_certificate_image", return_value=result) as vision, \
         patch("education_certificate._invoke_model") as text_model:
        actual = recognize_certificate_pdf("unused.pdf", {}, "", text_extractor=lambda _: "公司资料归档，仅供审核参考。" * 5)
    assert actual == result
    vision.assert_called_once()
    text_model.assert_not_called()


def test_two_submission_waves_reuse_browser_even_if_original_tab_closed():
    from gui_main import BossFilterGUI
    gui = object.__new__(BossFilterGUI)
    gui._education_browser_lock = RLock()
    base = Mock(url="https://www.chsi.com.cn/xlcx/query")
    first = Mock(url="https://www.chsi.com.cn/xlcx/result")
    second = Mock(url="about:blank")
    configure_owned_browser(base, first)
    configure_owned_browser(first, second)
    gui.browser_page = base
    gui.education_tabs = {}
    gui._is_browser_page_alive = lambda page: page is base or page is first or page is second
    gui._try_reconnect_browser = Mock(return_value=False)
    gui._create_fresh_browser_page = Mock()
    gui._get_education_tab(None)
    assert gui._get_education_tab("first") is first
    gui._is_browser_page_alive = lambda page: page is first or page is second
    gui._get_education_tab(None)
    assert gui._get_education_tab("second") is second
    gui._create_fresh_browser_page.assert_not_called()
    assert gui.browser_page is first
    assert gui.education_tabs == {"first": first, "second": second}


def test_manual_number_change_detaches_old_result_and_preserves_source_file():
    from gui_main import BossFilterGUI
    gui = object.__new__(BossFilterGUI)
    gui.education_items = {"a": dict(ready(), status="核验结果已生成", screenshot_path="existing.png")}
    gui.education_tabs = {"a": object()}
    gui.education_current_id = "a"
    gui.education_name_var = Mock(get=lambda: "测试甲")
    gui.education_number_var = Mock(get=lambda: "99999999999999999")
    gui._update_education_queue_row = Mock()
    gui._refresh_education_batch_status = Mock()
    gui._refresh_education_action_states = Mock()
    gui._save_current_education_fields()
    item = gui.education_items["a"]
    assert not gui.education_tabs
    assert item["query_revision"] == 1 and item["screenshot_path"] == ""
    assert item["manual_fields"]["certificate_number"] == "99999999999999999"
    assert item["status"] == "信息已修改"


def test_closed_browser_restores_unfinished_record_for_resubmission():
    from gui_main import BossFilterGUI
    gui = object.__new__(BossFilterGUI)
    gui._education_browser_lock = RLock()
    gui.education_items = {"a": dict(ready(), status="等待扫码"), "done": dict(ready(), status="核验结果已生成")}
    gui.education_tabs = {"a": object()}
    gui.education_current_id = None
    gui._is_browser_page_alive = lambda _: False
    gui._update_education_queue_row = Mock()
    gui._refresh_education_queue_summary = Mock()
    gui.run_on_ui = lambda callback: callback()
    with patch.object(Controller, "wait_for_result_page", return_value=False):
        gui._watch_education_result_page("a", gui.education_tabs["a"], "测试甲")
    assert gui.education_items["a"]["status"] == "打开失败"
    assert "重新执行第 2 步" in gui.education_items["a"]["detail"]
    assert Controller.verification_item_ids(gui.education_items) == ("a",)
    assert not gui.education_tabs


def test_old_watcher_cannot_overwrite_new_query_attempt():
    from gui_main import BossFilterGUI
    gui = object.__new__(BossFilterGUI)
    gui._education_browser_lock = RLock()
    gui.education_items = {"a": dict(ready(), status="等待扫码", query_revision=1)}
    gui.education_tabs = {"a": object()}
    gui.education_current_id = None
    gui._is_browser_page_alive = lambda _: False
    gui._update_education_queue_row = Mock()
    gui._refresh_education_queue_summary = Mock()
    callbacks = []
    gui.run_on_ui = callbacks.append
    with patch.object(Controller, "wait_for_result_page", return_value=False):
        gui._watch_education_result_page("a", gui.education_tabs["a"], "测试甲")
    gui.education_items["a"]["query_revision"] = 2
    for callback in callbacks:
        callback()
    assert gui.education_items["a"]["status"] == "等待扫码"
    gui._update_education_queue_row.assert_not_called()


def test_duplicate_certificates_ignore_names_paths_and_number_whitespace():
    items = {
        "a": dict(ready(), certificate_number="ab 123456", path="first.pdf"),
        "b": dict(ready(), certificate_number="AB123456", name="另一识别姓名", path="copy.jpg"),
        "degree": dict(ready(), certificate_type="degree", certificate_number="AB123456"),
        "unknown": dict(ready(), certificate_type="unknown", certificate_number="AB123456"),
        "empty": dict(ready(), certificate_number=""),
    }
    assert Controller.duplicate_certificate_ids(items) == {"a": ("b",), "b": ("a",)}
    assert items["b"]["name"] == "另一识别姓名"
    items["b"]["certificate_number"] = "AB123457"
    assert Controller.duplicate_certificate_ids(items) == {}
    items["b"]["certificate_number"] = "AB123456"
    items.pop("a")
    assert Controller.duplicate_certificate_ids(items) == {}


def test_duplicate_notice_refreshes_after_removing_a_record():
    from gui_main import BossFilterGUI
    gui = object.__new__(BossFilterGUI)
    gui.education_items = {"a": dict(ready(), path="a.pdf"), "b": dict(ready(), path="b.jpg")}
    gui.education_current_id = "a"
    gui.education_duplicate_notice_var = Mock()
    gui._update_education_queue_row = Mock()
    gui._refresh_education_batch_status()
    notice = gui.education_duplicate_notice_var.set.call_args.args[0]
    assert "疑似重复证书 2 项" in notice and "b.jpg" in notice
    assert len(gui.education_items) == 2
    gui.education_items.pop("b")
    gui._refresh_education_batch_status()
    gui.education_duplicate_notice_var.set.assert_called_with("")
    assert gui._education_duplicate_ids == set()


def test_result_monitor_continues_beyond_old_limit_with_backoff():
    reads = []
    delays = []
    def read(_page):
        reads.append(1)
        return "完成" if len(reads) == 1802 else "等待扫码"
    assert Controller.wait_for_result_page(
        object(), "测试", page_alive=lambda _: True, read_text=read,
        is_result_text=lambda text, _: text == "完成", sleep=delays.append,
        max_checks=None, poll_interval=lambda check: 1 if check < 60 else 10,
    )
    assert len(reads) == 1802
    assert sum(delays) > 1800


def test_result_monitor_reconnects_original_page_after_transient_failure():
    page = object()
    alive = [False]
    def recover(candidate):
        assert candidate is page
        alive[0] = True
        return True
    reconnect = Mock(side_effect=recover)
    assert Controller.wait_for_result_page(
        page, "测试", page_alive=lambda _: alive[0], read_text=lambda _: "完成",
        is_result_text=lambda *_: True, sleep=lambda _: None, max_checks=None,
        max_unavailable_checks=3, recover_page=reconnect,
    )
    reconnect.assert_called_once_with(page)


def test_result_monitor_stops_promptly_during_long_backoff():
    stopped = [False]
    reads = Mock(return_value="等待扫码")
    def sleep(_):
        stopped[0] = True
    assert not Controller.wait_for_result_page(
        object(), "测试", page_alive=lambda _: True, read_text=reads,
        is_result_text=lambda *_: False, sleep=sleep, max_checks=None,
        should_stop=lambda: stopped[0], poll_interval=lambda _: 10,
    )
    reads.assert_called_once()


def test_result_monitor_bounds_recovery_when_reading_stays_broken():
    reconnect = Mock(return_value=False)
    reads = Mock(side_effect=RuntimeError("连接中断"))
    assert not Controller.wait_for_result_page(
        object(), "测试", page_alive=lambda _: True, read_text=reads,
        is_result_text=lambda *_: False, sleep=lambda _: None, max_checks=None,
        max_unavailable_checks=3, recover_page=reconnect,
    )
    assert reads.call_count == 3
    reconnect.assert_called_once()


def test_tab_recovery_never_reconnects_shared_browser():
    from browser_controller import BrowserController
    page = Mock()
    page.tab_id = "original-tab"
    page.run_cdp.side_effect = [RuntimeError("driver closed"), {}]
    assert BrowserController.recover_tab_connection(page)
    page.disconnect.assert_called_once()
    page._driver_init.assert_called_once_with("original-tab")
    page.reconnect.assert_not_called()
    page.browser.reconnect.assert_not_called()


def test_tab_recovery_leaves_navigation_context_alone():
    from browser_controller import BrowserController
    page = Mock()
    assert BrowserController.recover_tab_connection(page)
    page.disconnect.assert_not_called()
    page.browser.reconnect.assert_not_called()


def test_new_tab_readiness_retries_and_retains_root_error():
    from browser_controller import BrowserController
    page = Mock()
    page.run_js.side_effect = [RuntimeError("context switching"), 1]
    sleep = Mock()
    BrowserController.wait_for_tab_ready(page, sleep=sleep)
    assert page.run_js.call_count == 2
    sleep.assert_called_once_with(0.3)
    error = RuntimeError("disconnected target")
    page.run_js.side_effect = error
    try:
        BrowserController.wait_for_tab_ready(page, sleep=sleep)
    except RuntimeError as caught:
        assert caught.__cause__ is error
    else:
        raise AssertionError("Persistent connection failure must be reported")


def test_failed_new_tab_is_closed_without_launching_another_browser():
    from gui_main import BossFilterGUI
    gui = object.__new__(BossFilterGUI)
    base = Mock(url="https://www.chsi.com.cn/")
    tab = Mock()
    tab.run_js.side_effect = RuntimeError("not ready")
    browser, targets = configure_owned_browser(base, tab)
    gui.browser_page = base
    gui.education_tabs = {}
    gui._is_browser_page_alive = lambda candidate: candidate is base
    gui._create_fresh_browser_page = Mock()
    with patch("gui_main.time.sleep"):
        try:
            gui._get_education_tab_locked("a")
        except RuntimeError as caught:
            assert caught.__cause__.__cause__ is not None
        else:
            raise AssertionError("Failed tab must be reported")
    assert not targets
    assert sum(call.args[0] == "Target.createTarget" for call in browser._run_cdp.call_args_list) == 1
    assert "a" not in gui.education_tabs
    gui._create_fresh_browser_page.assert_not_called()


def configure_owned_browser(base, tab, *, fail_before_return=False, close_success=True):
    targets = {}
    def command(method, **kwargs):
        if method == "Target.createTarget":
            targets["owned"] = kwargs["url"]
            if fail_before_return:
                raise RuntimeError("created but response lost")
            return {"targetId": "owned"}
        if method == "Target.getTargets":
            return {"targetInfos": [{"targetId": key, "url": value} for key, value in targets.items()]}
        if method == "Target.closeTarget":
            if close_success:
                targets.pop(kwargs["targetId"], None)
            return {"success": close_success}
        raise AssertionError(method)
    base.browser._run_cdp.side_effect = command
    base.browser.get_tab.return_value = tab
    return base.browser, targets


def test_owned_creation_cleans_up_when_response_is_lost_before_object_exists():
    from browser_controller import BrowserController
    base = Mock()
    browser, targets = configure_owned_browser(base, Mock(), fail_before_return=True)
    targets["user-page"] = "about:blank"
    ownership = {}
    try:
        BrowserController.create_owned_tab(base, ownership=ownership, item_id="a", sleep=lambda _: None)
    except RuntimeError as error:
        assert "response lost" in str(error.__cause__)
    else:
        raise AssertionError("Expected creation failure")
    assert targets == {"user-page": "about:blank"}
    assert ownership == {}
    browser.get_tab.assert_not_called()


def test_failed_cleanup_retains_ownership_and_blocks_duplicate_creation():
    from browser_controller import BrowserController
    base = Mock()
    browser, targets = configure_owned_browser(base, Mock(), fail_before_return=True, close_success=False)
    ownership = {}
    for _ in range(2):
        try:
            BrowserController.create_owned_tab(base, ownership=ownership, item_id="a", sleep=lambda _: None)
        except RuntimeError:
            pass
        else:
            raise AssertionError("Cleanup failure must not be hidden")
    assert len(targets) == 1 and "a" in ownership
    assert sum(call.args[0] == "Target.createTarget" for call in browser._run_cdp.call_args_list) == 1


def test_browser_snapshot_is_shared_but_expires():
    from education_controller import EducationBrowserSnapshotCache
    now = [0.0]
    cache = EducationBrowserSnapshotCache(lambda: now[0])
    reader = Mock(return_value={"text": "waiting"})
    for _ in range(5):
        assert cache.read("one-tab", reader)["text"] == "waiting"
    reader.assert_called_once()
    now[0] = 1.0
    reader.return_value = {"text": "ready"}
    assert cache.read("one-tab", reader)["text"] == "ready"
    assert reader.call_count == 2


def test_budget_deadline_returns_without_waiting_for_slow_response():
    import threading
    import time
    import education_certificate as certificate
    release = threading.Event()
    closed = threading.Event()
    response = Mock()
    response.close.side_effect = closed.set
    def slow_post(*args, **kwargs):
        release.wait(3)
        return response
    @certificate._bounded_recognition
    def request():
        return certificate._post_with_recognition_budget("local-test", headers={}, body={}, timeout=2)
    with patch.object(certificate.requests, "post", side_effect=slow_post):
        start = time.monotonic()
        try:
            request(total_timeout=0.15)
        except certificate.RecognitionDeadlineExceeded:
            pass
        else:
            raise AssertionError("Deadline must be enforced")
        finally:
            release.set()
        assert time.monotonic() - start < 1
        assert closed.wait(1), "Late response must be closed and discarded"


def test_stop_interrupts_wait_for_active_model_request():
    import threading
    import education_certificate as certificate
    stop = threading.Event()
    release = threading.Event()
    response = Mock()
    closed = threading.Event()
    response.close.side_effect = closed.set
    def slow_post(*args, **kwargs):
        stop.set()
        release.wait(3)
        return response
    @certificate._bounded_recognition
    def request():
        return certificate._post_with_recognition_budget("local-test", headers={}, body={}, timeout=2)
    with patch.object(certificate.requests, "post", side_effect=slow_post):
        try:
            request(stop_event=stop)
        except certificate.RecognitionStopped:
            pass
        else:
            raise AssertionError("Stop must cancel local waiting")
        finally:
            release.set()
        assert closed.wait(1)
