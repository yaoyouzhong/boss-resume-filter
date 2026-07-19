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


def test_windows_credential_backend_is_a_declared_packaging_dependency():
    """The direct Windows credential path must exist in the formal build environment."""
    requirements = (BASE_DIR / "requirements.txt").read_text(encoding="utf-8")
    build_source = (BASE_DIR / "build.py").read_text(encoding="utf-8")

    assert 'pywin32-ctypes>=0.2.3; sys_platform == "win32"' in requirements
    assert '"win32ctypes.pywin32": "pywin32-ctypes"' in build_source


def test_release_workflow_rebuilds_macos_when_dmg_is_missing():
    """macOS release completeness requires both the auto-update ZIP and installer DMG."""
    workflow = (BASE_DIR / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")

    assert "BOSS_ResumeFilter_mac.zip" in workflow
    assert "BOSS_ResumeFilter.dmg" in workflow


def test_release_workflow_stages_only_github_after_both_platform_builds():
    """Hosted Actions must stop after the complete GitHub Draft is staged."""
    workflow = (BASE_DIR / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")

    assert "needs: [prepare, build_windows, build_macos]" in workflow
    assert "runs-on: windows-latest" in workflow
    assert "scripts/release_ci.py stage-github" in workflow
    assert "GITEE_TOKEN" not in workflow
    assert "finalize-local" not in workflow


def test_release_workflow_requires_one_explicit_authorization_and_never_auto_triggers():
    """Merging master must not publish; one manual authorization starts the full workflow."""
    workflow = (BASE_DIR / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")

    assert "workflow_dispatch:" in workflow
    assert "authorization:" in workflow
    assert "Exact authorization text: 确认正式发布 vX.Y" in workflow
    assert "content_sha:" in workflow
    assert "--approved-content-sha" in workflow
    assert "dry_run:" in workflow
    assert "push:" not in workflow
    assert "pull_request:" not in workflow
    assert "cancel-in-progress: false" in workflow
    assert "queue: max" in workflow
    assert "python scripts/release_ci.py prepare" in workflow
    assert "python build.py --ci --release --strict-changelog" in workflow
    assert "--prepared-sha" in workflow
    assert '--authorization "${{ inputs.authorization }}"' not in workflow
    assert '--authorization "$env:RELEASE_AUTHORIZATION"' in workflow


def test_local_release_entrypoint_cannot_race_the_hosted_release_workflow():
    """Formal releases keep build.py disabled outside the orchestrated phases."""
    build_source = (BASE_DIR / "build.py").read_text(encoding="utf-8")

    assert "if args.release and not args.ci:" in build_source
    assert "本地 build.py --release 已停用" in build_source
    assert "python scripts/release_dispatch.py" in build_source


def test_pr_checks_run_stable_validation_for_master_pull_requests():
    """Every master-bound PR should compile and run both stable test entrypoints."""
    workflow = (BASE_DIR / ".github" / "workflows" / "pr-checks.yml").read_text(
        encoding="utf-8"
    )

    assert "name: PR Checks" in workflow
    assert "pull_request:" in workflow
    assert "branches: [master]" in workflow
    assert "runs-on: windows-latest" in workflow
    assert "uses: actions/checkout@v6" in workflow
    assert "uses: actions/setup-python@v6" in workflow
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
