import importlib.util
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch


BASE_DIR = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "boss_resume_filter_release_content_review",
    BASE_DIR / "scripts" / "release_content_review.py",
)
assert SPEC and SPEC.loader
review = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(review)


@contextmanager
def _raises(message: str):
    try:
        yield
    except review.ReleaseContentReviewError as exc:
        assert message in str(exc)
    else:
        raise AssertionError(message)


def test_content_sha_binds_version_commit_title_and_body_deterministically():
    values = ("2.24", "a" * 40, "v2.24 — Test", "### 新增功能\n\n- **A**：B")
    digest = review.content_sha256(*values)
    assert digest == review.content_sha256(*values)
    assert digest != review.content_sha256("2.24", "b" * 40, values[2], values[3])
    assert digest != review.content_sha256("2.24", values[1], "v2.24 — Changed", values[3])


def test_review_blocks_user_facing_style_warnings_but_not_manifest_lag():
    style = type("Issue", (), {
        "severity": "warning",
        "title": "CHANGELOG 含内部实现表述",
        "detail": "Icon",
    })()
    manifest = type("Issue", (), {
        "severity": "warning",
        "title": "latest.json 版本滞后",
        "detail": "expected before publication",
    })()
    with (
        patch.object(review.build, "_extract_changelog_release", return_value=("v2.24 — Test", "body")),
        patch.object(review, "audit_user_facing_release", return_value=[manifest]),
    ):
        result = review.review_release_content("2.24", "a" * 40)
    assert result["review_warnings"] == ["latest.json 版本滞后"]

    with (
        patch.object(review.build, "_extract_changelog_release", return_value=("v2.24 — Test", "body")),
        patch.object(review, "audit_user_facing_release", return_value=[style]),
        _raises("发布内容审核未通过"),
    ):
        review.review_release_content("2.24", "a" * 40)


def test_approval_rejects_missing_or_stale_digest():
    evidence = {"content_sha": "c" * 64}
    review.require_approved_content(evidence, "c" * 64)
    with _raises("缺少发布内容确认凭证"):
        review.require_approved_content(evidence, "")
    with _raises("必须重新展示并确认"):
        review.require_approved_content(evidence, "d" * 64)
