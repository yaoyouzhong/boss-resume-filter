"""Deterministic review and approval binding for public release content."""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any


BASE_DIR = Path(__file__).resolve().parents[1]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

import build  # noqa: E402
from release_user_audit import audit_user_facing_release  # noqa: E402


class ReleaseContentReviewError(RuntimeError):
    """The final user-facing release content is not approved or reviewable."""


def _normalize(text: str) -> str:
    return "\n".join(line.rstrip() for line in text.replace("\r\n", "\n").replace("\r", "\n").strip().splitlines())


def content_sha256(version: str, release_sha: str, title: str, body: str) -> str:
    """Bind one approval to the exact version, commit, title, and body."""
    payload = json.dumps(
        [version, release_sha.strip().lower(), _normalize(title), _normalize(body)],
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _review_user_facing_content(version: str) -> tuple[str, str, list[str]]:
    """Extract the current release text and enforce the fixed user-facing audit."""
    title, body = build._extract_changelog_release(version)
    issues = audit_user_facing_release(BASE_DIR)
    blocking = [
        issue for issue in issues
        if issue.severity == "error" or issue.title.startswith("CHANGELOG ")
    ]
    if blocking:
        details = "; ".join(f"{issue.title}: {issue.detail}" for issue in blocking)
        raise ReleaseContentReviewError(f"发布内容审核未通过：{details}")
    warnings = [issue.title for issue in issues if issue.severity == "warning"]
    return title, body, warnings


def review_release_content(version: str, release_sha: str) -> dict[str, Any]:
    """Run the fixed user-facing audit and return immutable review evidence."""
    title, body, warnings = _review_user_facing_content(version)
    return {
        "release_title": title,
        "release_body": body,
        "content_sha": content_sha256(version, release_sha, title, body),
        "review_warnings": warnings,
    }


def review_release_candidate(
    version: str,
    candidate_sha: str,
    candidate_tree_sha: str,
) -> dict[str, Any]:
    """Bind human approval to the exact candidate tree before Squash merge."""
    title, body, warnings = _review_user_facing_content(version)
    return {
        "release_title": title,
        "release_body": body,
        "candidate_sha": candidate_sha,
        "candidate_tree_sha": candidate_tree_sha,
        "content_sha": content_sha256(
            version, f"tree:{candidate_tree_sha}", title, body,
        ),
        "review_warnings": warnings,
    }


def require_approved_content(review: dict[str, Any], approved_content_sha: str) -> None:
    """Reject missing, malformed, or stale approval evidence."""
    expected = str(review["content_sha"])
    actual = approved_content_sha.strip().lower()
    if not actual:
        raise ReleaseContentReviewError("缺少发布内容确认凭证；请先预览并确认最终标题和正文")
    if actual != expected:
        raise ReleaseContentReviewError("发布内容或发布提交在确认后发生变化；必须重新展示并确认")
