"""Do not archive incomplete CHSI photos; retry through the existing button."""
import io
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import Mock, patch

from PIL import Image

from education_certificate import (
    CHSI_QUERY_URL, ChsiResultNotReadyError, capture_chsi_result_png,
    is_valid_chsi_screenshot, save_chsi_result_screenshot,
    wait_for_chsi_result_images,
)
from education_controller import EducationController


def _png(color):
    buffer = io.BytesIO()
    Image.new("RGB", (300, 400), color).save(buffer, format="PNG")
    return buffer.getvalue()


def test_photo_waits_until_loaded_and_stable():
    page = SimpleNamespace(run_js=Mock(side_effect=[
        {"ready": False, "count": 0},
        {"ready": False, "count": 1},
        {"ready": True, "count": 1},
        {"ready": True, "count": 1},
        {"ready": True, "count": 1},
    ]))
    with patch("time.sleep") as sleep:
        wait_for_chsi_result_images(page)
    assert page.run_js.call_count == 5
    assert sleep.call_count == 4


def test_photo_not_loaded_or_absent_blocks_screenshot():
    for image_count in (0, 1):
        def run_js(script, *_args):
            if "chsi-result-image-readiness" in script:
                return {"ready": False, "count": image_count}
            return {"matched": True}
        page = SimpleNamespace(set=SimpleNamespace(activate=lambda: None),
                               get_frames=lambda **_: [], run_js=run_js, get_screenshot=Mock())
        with patch("time.sleep") as sleep:
            try:
                capture_chsi_result_png(page, "测试甲")
            except ChsiResultNotReadyError as error:
                assert "照片" in str(error)
            else:
                raise AssertionError("An absent or unloaded photo must prevent capture")
        page.get_screenshot.assert_not_called()
        assert sleep.call_count == 20
        assert sum(call.args[0] for call in sleep.call_args_list) == 5.0


def test_photo_readiness_failure_does_not_assume_ready():
    with patch("time.sleep"):
        try:
            wait_for_chsi_result_images(SimpleNamespace(run_js=lambda _: None))
        except ChsiResultNotReadyError:
            pass
        else:
            raise AssertionError("Unreadable readiness must not allow a screenshot")


def test_repeated_batch_click_replaces_only_after_photo_ready():
    with TemporaryDirectory() as folder:
        target = Path(folder) / "测试甲_学历核验_20260910_153025.png"
        save_chsi_result_screenshot(_png("red"), target)
        original = target.read_bytes()
        items = {"one": dict(name="测试甲", certificate_number="123456789012345678",
                              certificate_type="education", screenshot_filename=target.name,
                              screenshot_directory=str(Path(folder).resolve()))}
        capture = Mock(side_effect=ChsiResultNotReadyError("照片还在加载"))
        kwargs = dict(filename_builder=Mock(), existing_validator=is_valid_chsi_screenshot,
                      page_alive=lambda _: True, capture=capture, save=save_chsi_result_screenshot,
                      replace=lambda raw, path: save_chsi_result_screenshot(raw, path, replace_existing=True),
                      is_not_ready_error=lambda error: isinstance(error, ChsiResultNotReadyError))
        pages = {"one": SimpleNamespace(url=CHSI_QUERY_URL)}
        pending = EducationController.capture_result_screenshots(items, ["one"], pages, folder, **kwargs)
        assert pending.pending == 1
        assert "原截图已保留" in pending.items[0].detail
        assert target.read_bytes() == original
        capture.side_effect = None
        capture.return_value = _png("blue")
        refreshed = EducationController.capture_result_screenshots(items, ["one"], pages, folder, **kwargs)
        assert refreshed.saved == 1 and refreshed.skipped == 0
        assert target.read_bytes() != original
        assert is_valid_chsi_screenshot(target)
        assert [p.name for p in Path(folder).iterdir()] == [target.name]


def test_replacement_failure_keeps_original_and_removes_only_its_temp_file():
    with TemporaryDirectory() as folder:
        target = Path(folder) / "result.png"
        save_chsi_result_screenshot(_png("red"), target)
        original = target.read_bytes()
        with patch("education_certificate.os.replace", side_effect=PermissionError("file busy")):
            try:
                save_chsi_result_screenshot(_png("blue"), target, replace_existing=True)
            except RuntimeError as error:
                assert "原截图已保留" in str(error)
            else:
                raise AssertionError("A failed replacement must be reported")
        assert target.read_bytes() == original
        assert list(Path(folder).iterdir()) == [target]
