"""Degree/diploma routing and result isolation across both application modes."""
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import RLock
from types import SimpleNamespace
from unittest.mock import Mock, patch

from education_certificate import (
    CHSI_DEGREE_QUERY_URL, CHSI_QUERY_URL, build_chsi_screenshot_filename,
    chsi_query_url, classify_chsi_page_state, fill_chsi_query_page,
    prepare_chsi_screenshot_filenames,
    is_chsi_result_text, normalize_recognition, recognize_certificate_image,
    recognize_certificate_pdf, validate_chsi_fields,
)
from education_controller import EducationController
from gui_main import BossFilterGUI


def _payload(kind="degree"):
    return dict(certificate_type=kind, name="测试甲", certificate_number="1234567890123456",
                school="测试大学", major="计算机科学", confidence=96,
                rotation=0, rotation_confidence=99,
                field_confidence=dict(name=99, certificate_number=99, school=99, major=99))


def test_image_and_pdf_recognition_preserve_degree_kind_without_18_digit_bias():
    with patch("education_certificate._invoke_model", return_value=_payload()) as invoke, \
            patch("education_certificate.prepare_image_data_url", return_value="data:image/jpeg;base64,eA=="), \
            patch("education_certificate.prepare_orientation_sheet_data_url", return_value="data:image/jpeg;base64,eA=="):
        image = recognize_certificate_image("unused.png", {"model": "vision"}, "test")
        assert invoke.call_count == 1
        pdf = recognize_certificate_pdf("unused.pdf", {"model": "text"}, "test",
                                        text_extractor=lambda _: "测试大学学士学位证书，测试甲，计算机科学，证书编号1234567890123456")
    assert image.certificate_type == pdf.certificate_type == "degree"
    assert not any("18" in warning or "16 位" in warning for warning in image.warnings)


def test_unknown_type_requires_confirmation_before_query():
    items = {"one": {}}
    result = normalize_recognition(_payload("unknown"))
    EducationController.apply_recognition_results(items, {"one": (result, "")})
    assert items["one"]["status"] == "待人工确认"
    preparation = EducationController.prepare_chsi(items, ["one"], validator=validate_chsi_fields)
    assert not preparation.prepared
    assert preparation.invalid_ids == ("one",)
    assert "证书类型" in items["one"]["detail"]
    items["one"]["certificate_type"] = "degree"
    assert EducationController.prepare_chsi(items, ["one"], validator=validate_chsi_fields).prepared
    try:
        chsi_query_url("unknown")
    except ValueError:
        pass
    else:
        raise AssertionError("Unknown certificates must not default to diploma queries")


def test_both_hosts_route_degree_and_diploma_queries_and_retries():
    for standalone in (False, True):
        for kind, expected in (("degree", CHSI_DEGREE_QUERY_URL), ("education", CHSI_QUERY_URL)):
            gui = BossFilterGUI.__new__(BossFilterGUI)
            gui.standalone_education = standalone
            gui.education_items = {"one": {"certificate_type": kind}}
            gui._education_browser_lock = RLock()
            gui._education_navigation_slots = RLock()
            gui._is_browser_page_alive = lambda _: True
            gui._attempt_captcha_solve = Mock(side_effect=[(False, "待人工验证"), (True, "已提交查询")])
            page = SimpleNamespace(get=Mock(), run_js=Mock(return_value="ok"))
            assert gui._fill_and_solve_captcha(page, "测试甲", "1234567890123456", "one") == (True, "已提交查询")
            assert page.get.call_count == 2
            assert all(call.args == (expected,) for call in page.get.call_args_list)
            assert page.run_js.call_args.args[1:] == ("1234567890123456", "测试甲", kind)


def test_degree_form_navigation_and_missing_form_fail_clearly():
    page = SimpleNamespace(get=Mock(), run_js=Mock(return_value="ok"))
    fill_chsi_query_page(page, "测试甲", "1234567890123456", certificate_type="degree")
    page.get.assert_called_once_with(CHSI_DEGREE_QUERY_URL)
    page.run_js.return_value = "missing:zsbh"
    try:
        fill_chsi_query_page(page, "测试甲", "1234567890123456", certificate_type="degree")
    except RuntimeError as error:
        assert "页面结构已变化" in str(error)
    else:
        raise AssertionError("Missing form must not be reported as filled")


def test_degree_results_are_recognized_but_query_and_qr_pages_are_not():
    text = "测试甲 性别：男 出生日期：1990年 学位授予单位：测试大学 所学专业：计算机 学位授予日期：2012年 学位证书编号：1234567890123456"
    assert is_chsi_result_text(text, "测试甲")
    assert classify_chsi_page_state(dict(text=text, url=CHSI_DEGREE_QUERY_URL)) == "record"
    assert classify_chsi_page_state(dict(text="未查询到学位证书信息", url=CHSI_DEGREE_QUERY_URL)) == "not_found"
    assert not is_chsi_result_text("中国高等教育学位证书查询 姓名 证书编号 图片验证码", "测试甲")
    assert not is_chsi_result_text("测试甲 请使用学信网APP扫码", "测试甲")
    assert not is_chsi_result_text(text, "测试乙")


def test_mixed_same_name_results_cannot_cross_certificate_types():
    items = {kind: dict(name="测试甲", certificate_number="1234567890123456", certificate_type=kind)
             for kind in ("education", "degree")}
    pages = [SimpleNamespace(url=url, tab_id=kind) for kind, url in
             (("education", CHSI_QUERY_URL), ("degree", CHSI_DEGREE_QUERY_URL))]
    assigned = EducationController.assign_open_result_pages(
        items, tuple(items), pages, {}, page_alive=lambda _: True,
        read_text=lambda _: "测试甲 1234567890123456", is_result_text=lambda *_: True,
    )
    assert assigned["degree"] is pages[1]
    assert assigned["education"] is pages[0]
    assert not EducationController.result_page_matches_type(
        SimpleNamespace(url="https://example.com/xwcx/query.do"), items["degree"])


def test_mixed_screenshots_are_separate_and_repeatable_without_overwrite():
    items = {kind: dict(name="测试甲", certificate_number="1234567890123456", certificate_type=kind)
             for kind in ("education", "degree")}
    pages = {kind: SimpleNamespace(url=chsi_query_url(kind)) for kind in items}
    capture = Mock(return_value=b"test-image")
    def save(raw, path):
        path.write_bytes(raw)
        return path
    with TemporaryDirectory() as folder:
        names = prepare_chsi_screenshot_filenames(items, folder, captured_at="20260910_153025")
        for item_id, filename in names.items():
            items[item_id]["screenshot_filename"] = filename
            items[item_id]["screenshot_directory"] = str(Path(folder).resolve())
        kwargs = dict(filename_builder=build_chsi_screenshot_filename,
                      existing_validator=lambda p: p.read_bytes() == b"test-image",
                      page_alive=lambda _: True, capture=capture, save=save,
                      is_not_ready_error=lambda _: False)
        first = EducationController.capture_result_screenshots(items, tuple(items), pages, folder, **kwargs)
        second = EducationController.capture_result_screenshots(items, tuple(items), pages, folder, **kwargs)
        assert first.saved == second.skipped == 2
        names = [p.name for p in Path(folder).iterdir()]
        assert len(names) == 2
        assert any("学位核验" in name for name in names)
        assert any("学历核验" in name for name in names)
        assert capture.call_count == 2


def test_wrong_type_bound_tab_is_not_captured():
    item = dict(name="测试甲", certificate_number="1234567890123456", certificate_type="degree")
    capture = Mock()
    with TemporaryDirectory() as folder:
        result = EducationController.capture_result_screenshots(
            {"one": item}, ["one"], {"one": SimpleNamespace(url=CHSI_QUERY_URL)}, folder,
            filename_builder=build_chsi_screenshot_filename, existing_validator=lambda _: False,
            page_alive=lambda _: True, capture=capture, save=Mock(), is_not_ready_error=lambda _: False,
        )
    assert result.pending == 1
    capture.assert_not_called()


def test_changing_type_invalidates_completed_result_binding_and_screenshot():
    gui = BossFilterGUI.__new__(BossFilterGUI)
    gui.education_current_id = "one"
    gui.education_items = {"one": dict(name="测试甲", certificate_number="1234567890123456",
                                       certificate_type="education", status="核验结果已生成",
                                       screenshot_status="已保存", screenshot_path="existing.png")}
    gui.education_tabs = {"one": object()}
    gui.education_name_var = Mock(get=lambda: "测试甲")
    gui.education_number_var = Mock(get=lambda: "1234567890123456")
    gui.education_type_var = Mock(get=lambda: "学位证书")
    gui._update_education_queue_row = Mock()
    gui._refresh_education_batch_status = Mock()
    gui._refresh_education_action_states = Mock()
    gui._on_education_fields_edited()
    assert gui.education_items["one"]["certificate_type"] == "degree"
    assert gui.education_items["one"]["status"] == "信息已修改"
    assert gui.education_items["one"]["screenshot_path"] == ""
    assert not gui.education_tabs


def test_missing_model_type_is_not_assumed_to_be_a_diploma():
    payload = _payload()
    payload.pop("certificate_type")
    with patch("education_certificate._invoke_model", return_value=payload):
        result = recognize_certificate_pdf("unused.pdf", {"model": "text"}, "test",
                                           text_extractor=lambda _: "这是测试证书文本，仅测试字段处理，不含真实个人资料。")
    assert result.certificate_type == "unknown"


def test_timestamp_names_reserve_collisions_and_reuse_only_the_current_round():
    items = {"one": _payload(), "two": _payload()}
    items["two"]["certificate_number"] = "9999999999999999"
    with TemporaryDirectory() as folder:
        previous = Path(folder) / "测试甲_学位核验_20260910_153025.png"
        previous.write_bytes(b"existing-user-file")
        names = prepare_chsi_screenshot_filenames(items, folder, captured_at="20260910_153025")
        assert names == {
            "one": "测试甲_学位核验_20260910_153025_2.png",
            "two": "测试甲_学位核验_20260910_153025_3.png",
        }
        for item_id, filename in names.items():
            items[item_id]["screenshot_filename"] = filename
            items[item_id]["screenshot_directory"] = str(Path(folder).resolve())
        assert prepare_chsi_screenshot_filenames(items, folder, captured_at="20260910_163025") == names
        assert previous.read_bytes() == b"existing-user-file"
        with TemporaryDirectory() as new_folder:
            new_names = prepare_chsi_screenshot_filenames(items, new_folder, captured_at="20260910_163025")
            assert new_names["one"] == "测试甲_学位核验_20260910_163025.png"
        EducationController.apply_recognition_results(items, {"one": (normalize_recognition(_payload()), "")})
        assert "screenshot_filename" not in items["one"]
        fresh = prepare_chsi_screenshot_filenames(items, folder, captured_at="20260910_163025")
        assert fresh["one"] == "测试甲_学位核验_20260910_163025.png"
