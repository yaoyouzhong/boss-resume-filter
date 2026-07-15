from pathlib import Path


BASE_DIR = Path(__file__).resolve().parents[2]


def test_runtime_import_dependencies_are_not_excluded_from_pyinstaller():
    """Keep import-time dependencies in the frozen app.

    DrissionPage imports sqlite3/DataRecorder and lxml.html during normal startup.
    Excluding any of these makes GUI actions fail inside the packaged EXE even
    when source-mode tests pass.
    """
    build_source = (BASE_DIR / "build.py").read_text(encoding="utf-8")
    forbidden_excludes = [
        "--exclude-module=sqlite3",
        "--exclude-module=lxml.html",
    ]

    for option in forbidden_excludes:
        assert option not in build_source


def test_pandas_is_not_a_packaging_dependency():
    """Excel export should stay on openpyxl to avoid bundling pandas and numpy."""
    requirements = (BASE_DIR / "requirements.txt").read_text(encoding="utf-8")
    build_source = (BASE_DIR / "build.py").read_text(encoding="utf-8")

    assert "pandas" not in requirements
    assert '"pandas": "pandas"' not in build_source
    assert "--exclude-module=numpy" in build_source
    assert "--exclude-module=numpy.libs" in build_source


def test_release_workflow_rebuilds_macos_when_dmg_is_missing():
    """macOS release completeness requires both the auto-update ZIP and installer DMG."""
    workflow = (BASE_DIR / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")

    assert "BOSS_ResumeFilter_mac\\.zip" in workflow
    assert "BOSS_ResumeFilter\\.dmg" in workflow


def test_release_workflow_does_not_upload_to_gitee_from_github_runner():
    """GitHub-hosted macOS runners are too slow for reliable Gitee large-file uploads."""
    workflow = (BASE_DIR / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")

    assert "GITEE_TOKEN: ${{ secrets.GITEE_TOKEN }}" not in workflow
    assert '--gitee-upload-local "$VERSION"' not in workflow


def test_pr_checks_run_stable_validation_for_master_pull_requests():
    """Every master-bound PR should compile and run both stable test entrypoints."""
    workflow = (BASE_DIR / ".github" / "workflows" / "pr-checks.yml").read_text(
        encoding="utf-8"
    )

    assert "name: PR Checks" in workflow
    assert "pull_request:" in workflow
    assert "branches: [master]" in workflow
    assert "runs-on: windows-latest" in workflow
    assert "PYTHONIOENCODING: utf-8" in workflow
    assert 'PYTHONUTF8: "1"' in workflow
    assert 'python -c "import build; build._check_source_compiles()"' in workflow
    assert "python tests/run_unit_tests.py" in workflow
    assert "python tests/test_import.py" in workflow


def test_pr_checks_never_publish_or_sync_releases():
    """PR validation must stay read-only and separate from release delivery."""
    workflow = (BASE_DIR / ".github" / "workflows" / "pr-checks.yml").read_text(
        encoding="utf-8"
    )

    assert "contents: read" in workflow
    assert "workflow_dispatch:" not in workflow
    assert "build.py --ci --release" not in workflow
    assert "softprops/action-gh-release" not in workflow
    assert "GITEE_TOKEN" not in workflow
