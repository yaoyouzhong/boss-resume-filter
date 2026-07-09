import json
import tempfile
from pathlib import Path

from release_user_audit import audit_user_facing_release, summarize_release_user_audit


def _write_project(base, *, readme=None, changelog=None, latest=None):
    Path(base, "gui_main.py").write_text('__version__ = "9.9.9"\n', encoding="utf-8")
    Path(base, "README.md").write_text(
        readme if readme is not None else (
            "> 当前发布版本：v9.9.9\n\n"
            "### v9.9.9 新增功能\n\n"
            "- **今日待办**：集中处理候选人后续动作。\n"
        ),
        encoding="utf-8",
    )
    Path(base, "CHANGELOG.md").write_text(
        changelog if changelog is not None else (
            "## v9.9.9 — 测试版本\n\n"
            "### 新增功能\n\n"
            "- **今日待办**：集中处理候选人后续动作。\n"
        ),
        encoding="utf-8",
    )
    Path(base, "latest.json").write_text(
        json.dumps(latest if latest is not None else {
            "version": "9.9.9",
            "release_notes": "### 新增功能\n\n- **今日待办**：集中处理候选人后续动作。",
            "assets": {"windows": {"name": "BOSS_ResumeFilter.exe"}},
        }, ensure_ascii=False),
        encoding="utf-8",
    )


def test_release_user_audit_passes_clean_minimal_project():
    with tempfile.TemporaryDirectory() as tmp:
        _write_project(tmp)
        issues = audit_user_facing_release(tmp)

    assert not [issue for issue in issues if issue.severity == "error"]


def test_release_user_audit_flags_internal_asset_and_style_keyword():
    with tempfile.TemporaryDirectory() as tmp:
        _write_project(
            tmp,
            changelog=(
                "## v9.9.9 — 测试版本\n\n"
                "### 体验优化\n\n"
                "- **内部说明**：优化 greet_context 持久化字段。\n"
            ),
            latest={
                "version": "9.9.9",
                "release_notes": "stale",
                "assets": {"readme": {"name": "README.md"}},
            },
        )
        issues = audit_user_facing_release(tmp)

    titles = {issue.title for issue in issues}
    assert "CHANGELOG 含内部实现表述" in titles
    assert "latest.json 暴露内部文件为下载资产" in titles
    assert "latest.json 更新说明未同步" in titles


def test_summarize_release_user_audit_reports_blocking_status():
    text = summarize_release_user_audit([
        type("Issue", (), {
            "severity": "error",
            "title": "阻断",
            "detail": "问题",
            "suggestion": "建议",
        })()
    ])

    assert "不建议发布" in text
