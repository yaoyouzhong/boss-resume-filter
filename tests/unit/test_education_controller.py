from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
import tempfile

from education_controller import EducationController


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
    assert batch.invalid_files == ("invalid.exe",)


def test_education_model_ref_overrides_default_only_when_complete():
    config = {
        "model": "default",
        "education_model_ref": {"model": "vision", "api_provider": "qwen"},
    }
    assert EducationController.resolve_api_config(config)["model"] == "vision"
    assert EducationController.resolve_api_config({"model": "default"})["model"] == "default"


def test_recognition_batch_and_apply_keep_failures_explicit():
    items = {
        "image": {"path": "a.png", "is_pdf": False},
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
    results = EducationController.recognize_documents(
        items,
        ("image", "pdf"),
        {"model": "vision"},
        "secret",
        recognize_image=lambda *_args: success,
        recognize_pdf=lambda *_args: (_ for _ in ()).throw(RuntimeError("bad pdf")),
    )
    updated = EducationController.apply_recognition_results(items, results)

    assert set(updated) == {"image", "pdf"}
    assert items["image"]["status"] == "已识别"
    assert items["pdf"]["status"] == "识别失败"
    assert items["pdf"]["warnings"] == "bad pdf"


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
        click_query=lambda _page: events.append("clicked"),
        check_result=lambda _page: (True, "ok"),
        resolve_vision_config=dict,
        sleep=lambda _seconds: None,
    )

    assert result.successful is True
    assert result.status == "已提交查询"
    assert events == ["ABCD", "clicked"]
