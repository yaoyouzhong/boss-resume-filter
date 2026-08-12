import contextlib
import inspect
import io
import json
import plistlib
import stat
import tempfile
from pathlib import Path
from unittest.mock import Mock, call, patch

import build
import updater


def test_update_macos_app_reads_real_plist_and_launches_replacement_script():
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        zip_path = root / "BOSS_ResumeFilter_mac.zip"
        zip_path.write_bytes(b"placeholder")
        current_app = root / "Applications" / "BOSS Resume Filter.app"
        extract_dir = root / "extracted"
        script_dir = root / "scripts"
        script_dir.mkdir()
        executable_path = (
            extract_dir
            / "BOSS Resume Filter.app"
            / "Contents"
            / "MacOS"
            / "BOSS_ResumeFilter"
        )

        def fake_ditto(command, **_kwargs):
            assert command[:4] == ["ditto", "-x", "-k", str(zip_path)]
            executable_path.parent.mkdir(parents=True)
            executable_path.write_bytes(b"#!/bin/sh\n")
            info_plist = executable_path.parents[1] / "Info.plist"
            with open(info_plist, "wb") as file_obj:
                plistlib.dump({"CFBundleExecutable": "BOSS_ResumeFilter"}, file_obj)
            return updater.subprocess.CompletedProcess(command, 0)

        with (
            patch.object(updater.tempfile, "mkdtemp", return_value=str(extract_dir)),
            patch.object(updater.tempfile, "gettempdir", return_value=str(script_dir)),
            patch.object(updater.subprocess, "run", side_effect=fake_ditto),
            patch.object(updater.subprocess, "Popen", return_value=Mock()) as popen,
        ):
            success, message = updater.update_macos_app(zip_path, current_app)

        assert success is True
        assert "重启" in message
        if updater.os.name != "nt":
            assert executable_path.stat().st_mode & stat.S_IXUSR
        script = (script_dir / "boss_update.sh").read_text(encoding="utf-8")
        assert "CFBundleExecutable" in script
        assert str(current_app) in script
        popen.assert_called_once()


def test_run_in_venv_relaunches_the_requested_release_entrypoint():
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        current_python = root / "system" / "python.exe"
        project_python = root / "pack_venv" / "Scripts" / "python.exe"
        entrypoint = root / "scripts" / "release_delivery.py"
        project_python.parent.mkdir(parents=True)
        project_python.touch()
        entrypoint.parent.mkdir(parents=True)
        entrypoint.touch()
        completed = build.subprocess.CompletedProcess([], 0)

        with (
            patch.object(build, "VENV_PYTHON", project_python),
            patch.object(build.sys, "executable", str(current_python)),
            patch.object(
                build.sys,
                "argv",
                ["release_delivery.py", "--version", "2.24"],
            ),
            patch.object(build.subprocess, "run", return_value=completed) as run,
        ):
            try:
                build.run_in_venv(entrypoint)
            except SystemExit as exc:
                assert exc.code == 0
            else:
                raise AssertionError("system Python must hand off to pack_venv")

    run.assert_called_once_with([
        str(project_python),
        str(entrypoint.resolve()),
        "--version",
        "2.24",
    ])


def test_release_note_recovery_never_commits_local_files_and_requires_final_verify():
    source = inspect.getsource(build._sync_release_notes)
    assert '"git", "commit"' not in source
    assert "_verify_release_remote_state(version)" in source
    assert "latest.json.release_notes" in source


def test_prepared_ci_build_is_bound_to_the_exact_workflow_commit():
    completed = build.subprocess.CompletedProcess(
        ["git", "rev-parse", "HEAD"], 0, stdout="a" * 40 + "\n", stderr="",
    )
    with (
        patch.dict(build.os.environ, {
            "GITHUB_ACTIONS": "true",
            "GITHUB_EVENT_NAME": "workflow_dispatch",
        }),
        patch.object(build.subprocess, "run", return_value=completed),
        patch.object(build, "_check_source_compiles") as compile_check,
        patch.object(build, "_check_undefined_names") as ruff_check,
    ):
        build._validate_prepared_ci_build("a" * 40)
    compile_check.assert_called_once_with()
    ruff_check.assert_called_once_with()

    with patch.dict(build.os.environ, {}, clear=True):
        try:
            build._validate_prepared_ci_build("a" * 40)
        except SystemExit as exc:
            assert exc.code == 1
        else:
            raise AssertionError("local callers must not skip the strict release gate")


def test_changelog_coverage_does_not_block_on_prompt_schema_or_internal_status():
    diff_text = """\
diff --git a/job_ai_parser.py b/job_ai_parser.py
--- a/job_ai_parser.py
+++ b/job_ai_parser.py
-        \" \\\"salary_min\\\": 可选整数或null},\\n\"
diff --git a/gui_main.py b/gui_main.py
--- a/gui_main.py
+++ b/gui_main.py
+        if resolution.status == \"confirmed\":
+        elif decision.review_status == \"passed\":
+        if decision.review_status != \"pending\":
"""

    class Result:
        returncode = 0
        stdout = diff_text

    originals = (
        build._read_version,
        build._extract_changelog_release,
        build._get_last_tag,
        build.subprocess.run,
    )
    try:
        build._read_version = lambda: "2.21"
        build._extract_changelog_release = lambda _version: (
            "v2.21 — 测试",
            "### 新增功能\n\n- 测试\n\n### 体验优化\n\n- 测试\n\n### 问题修复\n\n- 测试",
        )
        build._get_last_tag = lambda: "v2.20"
        build.subprocess.run = lambda *args, **kwargs: Result()

        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            build._check_code_to_changelog_coverage(strict=True)
    finally:
        (
            build._read_version,
            build._extract_changelog_release,
            build._get_last_tag,
            build.subprocess.run,
        ) = originals

    assert "未检测到用户可见的新增代码信号" in output.getvalue()
    assert "未覆盖信号" not in output.getvalue()


def test_changelog_coverage_ignores_internal_assignments_and_control_flow():
    diff_text = """\
diff --git a/bossmaster.py b/bossmaster.py
--- a/bossmaster.py
+++ b/bossmaster.py
+    retry_at = parsedate_to_datetime(raw)
+    clear_outcome = {"kept": 0, "removed": 0, "blacklisted": 0}
+    listener = page.listen
+    if detail_listener:
+        or any(mark in page_text for mark in risk_marks)
+        detail_listener.stop()
+        "risk_blocked",
diff --git a/gui_main.py b/gui_main.py
--- a/gui_main.py
+++ b/gui_main.py
-                if c.get('followup_status') not in {"不合适", "已归档"}:
+            if candidate.get('followup_status') not in {"不合适", "已归档"}:
"""

    class Result:
        returncode = 0
        stdout = diff_text

    originals = (
        build._read_version,
        build._extract_changelog_release,
        build._get_last_tag,
        build.subprocess.run,
    )
    try:
        build._read_version = lambda: "2.24.1"
        build._extract_changelog_release = lambda _version: (
            "v2.24.1 — 测试",
            "### 体验优化\n\n- 测试",
        )
        build._get_last_tag = lambda: "v2.24"
        build.subprocess.run = lambda *args, **kwargs: Result()

        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            build._check_code_to_changelog_coverage(strict=True)
    finally:
        (
            build._read_version,
            build._extract_changelog_release,
            build._get_last_tag,
            build.subprocess.run,
        ) = originals

    assert "未检测到用户可见的新增代码信号" in output.getvalue()
    assert "未覆盖信号" not in output.getvalue()


def test_changelog_coverage_ignores_stable_ids_and_redaction_regexes():
    diff_text = """\
diff --git a/bossmaster.py b/bossmaster.py
--- a/bossmaster.py
+++ b/bossmaster.py
+                {"job_name": job_name, "job_uuid": job_uuid},
diff --git a/diagnostic_package.py b/diagnostic_package.py
--- a/diagnostic_package.py
+++ b/diagnostic_package.py
+    re.compile(r"(\\b正在向\\s+)([^\\s]+)(?=\\s+打招呼)"),
+        r"(\\[\\d+/\\d+\\]\\s+)([^\\s-]+)(?=\\s+(?:-|打招呼|\\())"
"""

    class Result:
        returncode = 0
        stdout = diff_text

    originals = (
        build._read_version,
        build._extract_changelog_release,
        build._get_last_tag,
        build.subprocess.run,
    )
    try:
        build._read_version = lambda: "2.24.6"
        build._extract_changelog_release = lambda _version: (
            "v2.24.6 — 测试",
            "### 新增功能\n\n- 测试\n\n### 体验优化\n\n- 测试",
        )
        build._get_last_tag = lambda: "v2.24.5"
        build.subprocess.run = lambda *args, **kwargs: Result()

        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            build._check_code_to_changelog_coverage(strict=True)
    finally:
        (
            build._read_version,
            build._extract_changelog_release,
            build._get_last_tag,
            build.subprocess.run,
        ) = originals

    assert "未检测到用户可见的新增代码信号" in output.getvalue()
    assert "未覆盖信号" not in output.getvalue()


def test_changelog_coverage_ignores_removed_nested_helper():
    diff_text = """\
diff --git a/gui_main.py b/gui_main.py
--- a/gui_main.py
+++ b/gui_main.py
-        def daily_headline(current_items):
"""

    class Result:
        returncode = 0
        stdout = diff_text

    originals = (
        build._read_version,
        build._extract_changelog_release,
        build._get_last_tag,
        build.subprocess.run,
    )
    try:
        build._read_version = lambda: "2.21"
        build._extract_changelog_release = lambda _version: (
            "v2.21 — 测试",
            "### 新增功能\n\n- 测试\n\n### 体验优化\n\n- 测试\n\n### 问题修复\n\n- 测试",
        )
        build._get_last_tag = lambda: "v2.20"
        build.subprocess.run = lambda *args, **kwargs: Result()

        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            build._check_code_to_changelog_coverage(strict=True)
    finally:
        (
            build._read_version,
            build._extract_changelog_release,
            build._get_last_tag,
            build.subprocess.run,
        ) = originals

    assert "未检测到用户可见的新增代码信号" in output.getvalue()
    assert "未覆盖信号" not in output.getvalue()


def test_changelog_coverage_ignores_ui_text_moved_within_one_file():
    diff_text = """\
diff --git a/gui_main.py b/gui_main.py
--- a/gui_main.py
+++ b/gui_main.py
-        ttk.Label(row3, text="工作地点:", font=self.font_label)
+        ttk.Label(row_location, text="工作地点:", font=self.font_label)
"""

    class Result:
        returncode = 0
        stdout = diff_text

    originals = (
        build._read_version,
        build._extract_changelog_release,
        build._get_last_tag,
        build.subprocess.run,
    )
    try:
        build._read_version = lambda: "2.24.4"
        build._extract_changelog_release = lambda _version: (
            "v2.24.4 — 测试",
            "### 体验优化\n\n- 测试",
        )
        build._get_last_tag = lambda: "v2.24.3"
        build.subprocess.run = lambda *args, **kwargs: Result()

        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            build._check_code_to_changelog_coverage(strict=True)
    finally:
        (
            build._read_version,
            build._extract_changelog_release,
            build._get_last_tag,
            build.subprocess.run,
        ) = originals

    assert "未检测到用户可见的新增代码信号" in output.getvalue()
    assert "未覆盖信号" not in output.getvalue()


def test_changelog_coverage_ignores_widget_variable_wiring_continuation():
    diff_text = """\
diff --git a/gui_main.py b/gui_main.py
--- a/gui_main.py
+++ b/gui_main.py
@@ -100,2 +100,3 @@
-            textvariable=self.greet_context_capture_limit_var, font=_spin_font,
+                self, "greet_context_capture_limit_var", None
+            textvariable=self.greet_context_capture_limit_var,
"""

    class Result:
        returncode = 0
        stdout = diff_text

    originals = (
        build._read_version,
        build._extract_changelog_release,
        build._get_last_tag,
        build.subprocess.run,
    )
    try:
        build._read_version = lambda: "2.27"
        build._extract_changelog_release = lambda _version: (
            "v2.27 — 测试",
            "### 体验优化\n\n- 测试",
        )
        build._get_last_tag = lambda: "v2.26"
        build.subprocess.run = lambda *args, **kwargs: Result()

        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            build._check_code_to_changelog_coverage(strict=True)
    finally:
        (
            build._read_version,
            build._extract_changelog_release,
            build._get_last_tag,
            build.subprocess.run,
        ) = originals

    assert "未检测到用户可见的新增代码信号" in output.getvalue()
    assert "未覆盖信号" not in output.getvalue()


def test_changelog_coverage_ignores_ui_code_moved_between_files():
    diff_text = """\
diff --git a/gui_main.py b/gui_main.py
--- a/gui_main.py
+++ b/gui_main.py
-    rim_color = ui_theme.PRIMARY      # 品牌蓝边框
diff --git a/icons.py b/icons.py
--- a/icons.py
+++ b/icons.py
+    rim_color = ui_theme.PRIMARY      # 品牌蓝边框
"""

    class Result:
        returncode = 0
        stdout = diff_text

    originals = (
        build._read_version,
        build._extract_changelog_release,
        build._get_last_tag,
        build.subprocess.run,
    )
    try:
        build._read_version = lambda: "2.26"
        build._extract_changelog_release = lambda _version: (
            "v2.26 — 测试",
            "### 体验优化\n\n- 测试",
        )
        build._get_last_tag = lambda: "v2.25.2"
        build.subprocess.run = lambda *args, **kwargs: Result()

        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            build._check_code_to_changelog_coverage(strict=True)
    finally:
        (
            build._read_version,
            build._extract_changelog_release,
            build._get_last_tag,
            build.subprocess.run,
        ) = originals

    assert "未检测到用户可见的新增代码信号" in output.getvalue()
    assert "未覆盖信号" not in output.getvalue()


def test_changelog_coverage_ignores_excel_column_width_map_conversion():
    diff_text = """\
diff --git a/bossmaster.py b/bossmaster.py
--- a/bossmaster.py
+++ b/bossmaster.py
@@ -100,8 +100,10 @@
     column_widths = {
-        'F': 10,  # 工作年限
-        'N': 10,  # 匹配分
+        '工作年限': 10, '匹配分': 10,
+        '已打招呼': 12, '已回复': 10,
     }
-    for col, width in column_widths.items():
-        ws.column_dimensions[col].width = width
+    for cell in ws[1]:
+        width = column_widths.get(str(cell.value or ''))
+        ws.column_dimensions[cell.column_letter].width = width
"""

    class Result:
        returncode = 0
        stdout = diff_text

    originals = (
        build._read_version,
        build._extract_changelog_release,
        build._get_last_tag,
        build.subprocess.run,
    )
    try:
        build._read_version = lambda: "2.27"
        build._extract_changelog_release = lambda _version: (
            "v2.27 — 测试",
            "### 体验优化\n\n- 测试",
        )
        build._get_last_tag = lambda: "v2.26"
        build.subprocess.run = lambda *args, **kwargs: Result()

        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            build._check_code_to_changelog_coverage(strict=True)
    finally:
        (
            build._read_version,
            build._extract_changelog_release,
            build._get_last_tag,
            build.subprocess.run,
        ) = originals

    assert "未检测到用户可见的新增代码信号" in output.getvalue()
    assert "未覆盖信号" not in output.getvalue()


def test_changelog_coverage_maps_updater_install_waits_to_update_installation():
    diff_text = """\
diff --git a/updater.py b/updater.py
--- a/updater.py
+++ b/updater.py
@@ -30,2 +30,3 @@
+    UPDATE_TIMEOUT_HELPER_READY,
+    lambda path=cached_update: show_download_complete_actions(path),
"""

    class Result:
        returncode = 0
        stdout = diff_text

    originals = (
        build._read_version,
        build._extract_changelog_release,
        build._get_last_tag,
        build.subprocess.run,
    )
    try:
        build._read_version = lambda: "2.26"
        build._extract_changelog_release = lambda _version: (
            "v2.26 — 测试",
            "### 体验优化\n\n- 优化更新安装等待和缓存复用。",
        )
        build._get_last_tag = lambda: "v2.25.2"
        build.subprocess.run = lambda *args, **kwargs: Result()

        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            build._check_code_to_changelog_coverage(strict=True)
    finally:
        (
            build._read_version,
            build._extract_changelog_release,
            build._get_last_tag,
            build.subprocess.run,
        ) = originals

    assert "均已在 CHANGELOG 中体现" in output.getvalue()
    assert "未覆盖信号" not in output.getvalue()


def test_changelog_coverage_ignores_update_helper_cleanup_command():
    diff_text = """\
diff --git a/updater.py b/updater.py
--- a/updater.py
+++ b/updater.py
+        ["cmd", "/c", f'timeout /t 3 /nobreak >nul & rmdir /s /q "{temp_dir}"'],
"""

    class Result:
        returncode = 0
        stdout = diff_text

    originals = (
        build._read_version,
        build._extract_changelog_release,
        build._get_last_tag,
        build.subprocess.run,
    )
    try:
        build._read_version = lambda: "2.26"
        build._extract_changelog_release = lambda _version: (
            "v2.26 — 测试",
            "### 体验优化\n\n- 测试",
        )
        build._get_last_tag = lambda: "v2.25.2"
        build.subprocess.run = lambda *args, **kwargs: Result()

        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            build._check_code_to_changelog_coverage(strict=True)
    finally:
        (
            build._read_version,
            build._extract_changelog_release,
            build._get_last_tag,
            build.subprocess.run,
        ) = originals

    assert "未检测到用户可见的新增代码信号" in output.getvalue()
    assert "未覆盖信号" not in output.getvalue()


def test_changelog_coverage_groups_structured_dialog_copy_by_diff_hunk():
    diff_text = """\
diff --git a/gui_main.py b/gui_main.py
--- a/gui_main.py
+++ b/gui_main.py
@@ -10,5 +10,9 @@ def export_diagnostics(self):
-        messagebox.showinfo(
-            "导出完成",
-            "本次已禁止继续写入。\\n\\n此 ZIP 未加密，请妥善保管。",
+        messagebox.show_result(
+            "诊断包已导出",
+            headline="导出完成",
+            notice="此 ZIP 未加密，请妥善保管。",
+            detail="本次已禁止继续写入。",
         )
"""

    class Result:
        returncode = 0
        stdout = diff_text

    originals = (
        build._read_version,
        build._extract_changelog_release,
        build._get_last_tag,
        build.subprocess.run,
    )
    try:
        build._read_version = lambda: "2.25.1"
        build._extract_changelog_release = lambda _version: (
            "v2.25.1 — 测试",
            "### 体验优化\n\n- 统一弹窗层级与操作反馈。",
        )
        build._get_last_tag = lambda: "v2.25"
        build.subprocess.run = lambda *args, **kwargs: Result()

        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            build._check_code_to_changelog_coverage(strict=True)
    finally:
        (
            build._read_version,
            build._extract_changelog_release,
            build._get_last_tag,
            build.subprocess.run,
        ) = originals

    assert "均已在 CHANGELOG 中体现" in output.getvalue()
    assert "未覆盖信号" not in output.getvalue()


def test_changelog_coverage_still_blocks_unrelated_ui_feature_in_another_hunk():
    diff_text = """\
diff --git a/gui_main.py b/gui_main.py
--- a/gui_main.py
+++ b/gui_main.py
@@ -10,3 +10,7 @@ def export_diagnostics(self):
-        messagebox.showinfo("导出完成", "诊断包已导出")
+        messagebox.show_result(
+            "诊断包已导出",
+            headline="导出完成",
+        )
@@ -30,2 +34,3 @@ def create_result_page(self):
+        ttk.Button(toolbar, text="批量归档候选人")
"""

    class Result:
        returncode = 0
        stdout = diff_text

    originals = (
        build._read_version,
        build._extract_changelog_release,
        build._get_last_tag,
        build.subprocess.run,
    )
    try:
        build._read_version = lambda: "2.25.1"
        build._extract_changelog_release = lambda _version: (
            "v2.25.1 — 测试",
            "### 体验优化\n\n- 统一弹窗层级与操作反馈。",
        )
        build._get_last_tag = lambda: "v2.25"
        build.subprocess.run = lambda *args, **kwargs: Result()

        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            try:
                build._check_code_to_changelog_coverage(strict=True)
            except SystemExit as exc:
                assert exc.code == 1
            else:
                raise AssertionError("unrelated UI features must remain release-note blockers")
    finally:
        (
            build._read_version,
            build._extract_changelog_release,
            build._get_last_tag,
            build.subprocess.run,
        ) = originals

    assert "批量归档候选人" in output.getvalue()
    assert "未覆盖信号" in output.getvalue()


def _with_build_context(tmp_path, dist_dir, *, is_win, is_mac):
    class BuildContext:
        def __enter__(self):
            self.original_base_dir = build.BASE_DIR
            self.original_dist_dir = build.DIST_DIR
            self.original_build_state_path = build.BUILD_STATE_PATH
            self.original_is_win = build.IS_WIN
            self.original_is_mac = build.IS_MAC
            build.BASE_DIR = tmp_path
            build.DIST_DIR = dist_dir
            build.BUILD_STATE_PATH = tmp_path / ".build_state.json"
            build.IS_WIN = is_win
            build.IS_MAC = is_mac

        def __exit__(self, exc_type, exc, tb):
            build.BASE_DIR = self.original_base_dir
            build.DIST_DIR = self.original_dist_dir
            build.BUILD_STATE_PATH = self.original_build_state_path
            build.IS_WIN = self.original_is_win
            build.IS_MAC = self.original_is_mac

    return BuildContext()


def test_verify_downloaded_file_accepts_matching_size_and_sha256():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "asset.exe"
        path.write_bytes(b"MZboss-update")

        asset_info = {
            "size": path.stat().st_size,
            "sha256": updater._file_sha256(path),
        }

        ok, error = updater.verify_downloaded_file(path, asset_info)

    assert ok is True
    assert error is None


def test_verify_downloaded_file_rejects_size_mismatch():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "asset.exe"
        path.write_bytes(b"MZboss-update")

        asset_info = {
            "size": path.stat().st_size + 1,
            "sha256": updater._file_sha256(path),
        }
        ok, error = updater.verify_downloaded_file(path, asset_info)

    assert ok is False
    assert "文件大小不匹配" in error


def test_verify_downloaded_file_rejects_sha256_mismatch():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "asset.exe"
        path.write_bytes(b"MZboss-update")

        ok, error = updater.verify_downloaded_file(
            path,
            {"size": path.stat().st_size, "sha256": "0" * 64},
        )

    assert ok is False
    assert "SHA256 不匹配" in error


def test_verify_downloaded_file_rejects_missing_integrity_metadata():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "asset.exe"
        path.write_bytes(b"MZboss-update")

        ok, error = updater.verify_downloaded_file(path, {"size": path.stat().st_size})

    assert ok is False
    assert "缺少文件大小或 SHA256" in error


def test_verify_downloaded_file_rejects_invalid_exe_header():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "asset.exe"
        path.write_bytes(b"<html>not an exe</html>")

        asset_info = {
            "size": path.stat().st_size,
            "sha256": updater._file_sha256(path),
        }
        ok, error = updater.verify_downloaded_file(path, asset_info)

    assert ok is False
    assert "EXE 文件头无效" in error


def test_verify_downloaded_file_rejects_invalid_zip_header():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "asset.zip"
        path.write_bytes(b"<html>not a zip</html>")

        asset_info = {
            "size": path.stat().st_size,
            "sha256": updater._file_sha256(path),
        }
        ok, error = updater.verify_downloaded_file(path, asset_info)

    assert ok is False
    assert "ZIP 文件头无效" in error


def test_update_latest_json_writes_asset_metadata():
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        dist_dir = tmp_path / "dist"
        dist_dir.mkdir()
        (dist_dir / "BOSS_ResumeFilter.exe").write_bytes(b"exe")
        (dist_dir / "README.md").write_text("readme", encoding="utf-8")

        with _with_build_context(tmp_path, dist_dir, is_win=True, is_mac=False):
            build.update_latest_json("9.9.9", "notes", quiet=True)

            data = json.loads((tmp_path / "latest.json").read_text(encoding="utf-8"))
            expected_exe_sha256 = build._sha256_file(dist_dir / "BOSS_ResumeFilter.exe")

    assert data["assets"]["windows"]["size"] == 3
    assert data["assets"]["windows"]["sha256"] == expected_exe_sha256
    assert "readme" not in data["assets"]


def test_update_latest_json_writes_macos_update_asset_metadata():
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        dist_dir = tmp_path / "dist"
        dist_dir.mkdir()
        zip_path = dist_dir / "BOSS_ResumeFilter_mac.zip"
        dmg_path = dist_dir / "BOSS_ResumeFilter.dmg"
        zip_path.write_bytes(b"zip")
        dmg_path.write_bytes(b"dmg")

        with _with_build_context(tmp_path, dist_dir, is_win=False, is_mac=True):
            build.update_latest_json("9.9.9", "notes", quiet=True)

            data = json.loads((tmp_path / "latest.json").read_text(encoding="utf-8"))
            expected_zip_sha256 = build._sha256_file(zip_path)
            expected_dmg_sha256 = build._sha256_file(dmg_path)

    assert data["assets"]["macos"]["size"] == 3
    assert data["assets"]["macos"]["sha256"] == expected_zip_sha256
    assert data["assets"]["macos_dmg"]["size"] == 3
    assert data["assets"]["macos_dmg"]["sha256"] == expected_dmg_sha256


def test_latest_json_manifest_keeps_download_and_asset_keys_consistent():
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        dist_dir = tmp_path / "dist"
        dist_dir.mkdir()
        (dist_dir / "BOSS_ResumeFilter_mac.zip").write_bytes(b"zip")
        (dist_dir / "BOSS_ResumeFilter.dmg").write_bytes(b"dmg")

        downloads_cn = {
            "macos": "https://gitee.example/BOSS_ResumeFilter_mac.zip",
            "macos_dmg": "https://gitee.example/BOSS_ResumeFilter.dmg",
        }
        with _with_build_context(tmp_path, dist_dir, is_win=False, is_mac=True):
            build.update_latest_json("9.9.9", "notes", downloads_cn=downloads_cn, quiet=True)
            data = json.loads((tmp_path / "latest.json").read_text(encoding="utf-8"))

    update_asset_keys = {"windows", "macos", "macos_dmg"}
    assert set(data["downloads"]) >= update_asset_keys
    assert set(data["downloads_cn"]) <= set(data["downloads"])
    assert set(data["assets"]) <= update_asset_keys
    assert set(data["assets"]) <= set(data["downloads"])


def test_release_asset_metadata_from_remote_assets_uses_github_digest():
    metadata = build._release_asset_metadata_from_remote_assets([
        {
            "name": "BOSS_ResumeFilter_mac.zip",
            "size": 123,
            "digest": "sha256:" + "a" * 64,
        },
        {
            "name": "README.md",
            "size": 456,
            "digest": "sha256:" + "b" * 64,
        },
    ])

    assert metadata == {
        "macos": {
            "size": 123,
            "sha256": "a" * 64,
        }
    }


def test_release_workflow_only_runs_when_explicitly_dispatched():
    """A master merge must never publish without the separate release authorization."""
    workflow = (build.BASE_DIR / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")

    assert "workflow_dispatch:" in workflow
    assert "tags:" not in workflow
    assert "pull_request:" not in workflow
    assert "authorization:" in workflow
    assert "scripts/release_ci.py stage-github" in workflow
    assert "GITEE_TOKEN" not in workflow
    assert "requirements-build.txt" in workflow
    assert "cache-dependency-path: requirements-release.txt" in workflow
    assert "-r requirements-release.txt" in workflow
    assert not (
        build.BASE_DIR / ".github" / "workflows" / "gitee-upload-probe.yml"
    ).exists()


def test_gitee_sync_never_force_pushes_master():
    workflow = (build.BASE_DIR / ".github" / "workflows" / "sync-gitee.yml").read_text(encoding="utf-8")

    assert "git push gitee master:master" in workflow
    assert "--force" not in workflow


def test_ensure_github_release_asset_matches_local_reuploads_until_digest_matches():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "BOSS_ResumeFilter.exe"
        path.write_bytes(b"MZlocal-exe")
        expected_digest = build._sha256_file(path)
        calls = {"assets": 0, "uploads": 0}

        original_get_assets = build._get_github_release_assets
        original_upload = build._upload_github_release_asset
        original_sleep = build.time.sleep
        try:
            def fake_get_assets(tag):
                calls["assets"] += 1
                if calls["assets"] == 1:
                    return {
                        path.name: {
                            "name": path.name,
                            "size": path.stat().st_size + 1,
                            "digest": "sha256:" + "0" * 64,
                        }
                    }
                return {
                    path.name: {
                        "name": path.name,
                        "size": path.stat().st_size,
                        "digest": "sha256:" + expected_digest,
                    }
                }

            def fake_upload(tag, local_path, report=None):
                calls["uploads"] += 1
                return local_path.name

            build._get_github_release_assets = fake_get_assets
            build._upload_github_release_asset = fake_upload
            build.time.sleep = lambda _seconds: None

            ok = build._ensure_github_release_asset_matches_local(
                "v9.9.9",
                path,
                report=lambda _message: None,
                max_wait=1,
                poll_interval=0,
            )
        finally:
            build._get_github_release_assets = original_get_assets
            build._upload_github_release_asset = original_upload
            build.time.sleep = original_sleep

    assert ok is True
    assert calls["uploads"] == 1
    assert calls["assets"] >= 2


def test_verify_release_assets_complete_uses_size_without_downloading_gitee_assets():
    github_assets = {
        "BOSS_ResumeFilter.exe": {
            "name": "BOSS_ResumeFilter.exe",
            "size": 111,
            "digest": "sha256:" + "a" * 64,
        },
        "BOSS_ResumeFilter_mac.zip": {
            "name": "BOSS_ResumeFilter_mac.zip",
            "size": 222,
            "digest": "sha256:" + "b" * 64,
        },
        "BOSS_ResumeFilter.dmg": {
            "name": "BOSS_ResumeFilter.dmg",
            "size": 333,
            "digest": "sha256:" + "c" * 64,
        },
    }
    gitee_assets = {
        "BOSS_ResumeFilter.exe": {"id": 1, "size": 111},
        "BOSS_ResumeFilter_mac.zip": {"id": 2, "size": 222},
        "BOSS_ResumeFilter.dmg": {"id": 3, "size": 333},
    }
    release_cache = {
        "token": "token",
        "owner": "owner",
        "repo": "repo",
        "tag": "v9.9.9",
        "api_base": "https://gitee.example/api",
        "release_id": 1,
        "existing": {},
    }
    downloaded = []

    original_get_assets = build._get_github_release_assets
    original_fetch_assets = build._gitee_fetch_assets
    original_remote_sha = build._remote_file_sha256
    try:
        build._get_github_release_assets = lambda tag: github_assets
        build._gitee_fetch_assets = lambda api_base, token, release_id, retry_fn=None: gitee_assets

        build._remote_file_sha256 = lambda url, token=None: downloaded.append((url, token))

        ok = build._verify_release_assets_complete(
            "v9.9.9",
            release_cache=release_cache,
            report=lambda _message: None,
        )
    finally:
        build._get_github_release_assets = original_get_assets
        build._gitee_fetch_assets = original_fetch_assets
        build._remote_file_sha256 = original_remote_sha

    assert ok is True
    assert release_cache["existing"] == gitee_assets
    assert downloaded == []


def test_verify_release_assets_complete_rejects_missing_github_asset():
    github_assets = {
        "BOSS_ResumeFilter.exe": {
            "name": "BOSS_ResumeFilter.exe",
            "size": 111,
            "digest": "sha256:" + "a" * 64,
        },
        "BOSS_ResumeFilter_mac.zip": {
            "name": "BOSS_ResumeFilter_mac.zip",
            "size": 222,
            "digest": "sha256:" + "b" * 64,
        },
    }

    original_get_assets = build._get_github_release_assets
    try:
        build._get_github_release_assets = lambda tag: github_assets
        ok = build._verify_release_assets_complete(
            "v9.9.9",
            report=lambda _message: None,
        )
    finally:
        build._get_github_release_assets = original_get_assets

    assert ok is False


def test_verify_release_assets_complete_rejects_gitee_sha_mismatch():
    github_assets = {
        "BOSS_ResumeFilter.exe": {
            "name": "BOSS_ResumeFilter.exe",
            "size": 111,
            "digest": "sha256:" + "a" * 64,
        },
        "BOSS_ResumeFilter_mac.zip": {
            "name": "BOSS_ResumeFilter_mac.zip",
            "size": 222,
            "digest": "sha256:" + "b" * 64,
        },
        "BOSS_ResumeFilter.dmg": {
            "name": "BOSS_ResumeFilter.dmg",
            "size": 333,
            "digest": "sha256:" + "c" * 64,
        },
    }
    gitee_assets = {
        "BOSS_ResumeFilter.exe": {"id": 1, "size": 111},
        "BOSS_ResumeFilter_mac.zip": {"id": 2, "size": 222},
        "BOSS_ResumeFilter.dmg": {"id": 3, "size": 333},
    }
    release_cache = {
        "token": "token",
        "owner": "owner",
        "repo": "repo",
        "tag": "v9.9.9",
        "api_base": "https://gitee.example/api",
        "release_id": 1,
        "existing": {},
    }

    original_get_assets = build._get_github_release_assets
    original_fetch_assets = build._gitee_fetch_assets
    original_remote_sha = build._remote_file_sha256
    try:
        build._get_github_release_assets = lambda tag: github_assets
        build._gitee_fetch_assets = lambda api_base, token, release_id, retry_fn=None: gitee_assets
        build._remote_file_sha256 = lambda url, token=None: "0" * 64

        ok = build._verify_release_assets_complete(
            "v9.9.9",
            release_cache=release_cache,
            report=lambda _message: None,
            verify_gitee_sha256=True,
        )
    finally:
        build._get_github_release_assets = original_get_assets
        build._gitee_fetch_assets = original_fetch_assets
        build._remote_file_sha256 = original_remote_sha

    assert ok is False


def test_collect_github_release_asset_metadata_uses_remote_digest_before_download():
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        dist_dir = tmp_path / "dist"
        dist_dir.mkdir()
        (dist_dir / "BOSS_ResumeFilter.exe").write_bytes(b"exe")

        original_get_assets = build._get_github_release_assets
        original_wait = build._wait_for_github_release_assets
        original_download = build._download_from_github_release
        try:
            build._get_github_release_assets = lambda tag: {
                "BOSS_ResumeFilter_mac.zip": {
                    "name": "BOSS_ResumeFilter_mac.zip",
                    "size": 222,
                    "digest": "sha256:" + "b" * 64,
                },
                "BOSS_ResumeFilter.dmg": {
                    "name": "BOSS_ResumeFilter.dmg",
                    "size": 333,
                    "digest": "sha256:" + "c" * 64,
                },
            }
            build._wait_for_github_release_assets = lambda tag, names: (_ for _ in ()).throw(
                AssertionError("remote digest metadata should avoid waiting")
            )
            build._download_from_github_release = lambda tag, name, dest: (_ for _ in ()).throw(
                AssertionError("remote digest metadata should avoid downloading")
            )

            with _with_build_context(tmp_path, dist_dir, is_win=True, is_mac=False):
                metadata = build._collect_github_release_asset_metadata("9.9.9")
        finally:
            build._get_github_release_assets = original_get_assets
            build._wait_for_github_release_assets = original_wait
            build._download_from_github_release = original_download

    assert metadata["windows"]["size"] == 3
    assert metadata["macos"] == {"size": 222, "sha256": "b" * 64}
    assert metadata["macos_dmg"] == {"size": 333, "sha256": "c" * 64}


def test_gitee_asset_can_reuse_github_metadata_from_latest_json():
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        dist_dir = tmp_path / "dist"
        dist_dir.mkdir()
        (tmp_path / "latest.json").write_text(
            json.dumps({
                "assets": {
                    "macos": {
                        "size": 222,
                        "sha256": "b" * 64,
                    }
                }
            }),
            encoding="utf-8",
        )

        with _with_build_context(tmp_path, dist_dir, is_win=True, is_mac=False):
            reusable = build._gitee_asset_can_reuse_github_metadata(
                "BOSS_ResumeFilter_mac.zip",
                {"size": 222},
                {
                    "name": "BOSS_ResumeFilter_mac.zip",
                    "size": 222,
                    "digest": "sha256:" + "b" * 64,
                },
            )

    assert reusable is True


def test_sync_gitee_from_github_skips_download_when_remote_assets_are_reusable():
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        dist_dir = tmp_path / "dist"
        dist_dir.mkdir()
        (tmp_path / "latest.json").write_text(
            json.dumps({
                "assets": {
                    "macos": {"size": 222, "sha256": "b" * 64},
                    "macos_dmg": {"size": 333, "sha256": "c" * 64},
                }
            }),
            encoding="utf-8",
        )
        release_cache = {
            "token": "token",
            "owner": "owner",
            "repo": "repo",
            "tag": "v9.9.9",
            "api_base": "https://gitee.example/api",
            "release_id": 1,
            "existing": {
                "BOSS_ResumeFilter_mac.zip": {"id": 1, "size": 222},
                "BOSS_ResumeFilter.dmg": {"id": 2, "size": 333},
            },
        }

        original_fetch_assets = build._gitee_fetch_assets
        original_get_assets = build._get_github_release_assets
        original_download = build._download_from_github_release
        try:
            build._gitee_fetch_assets = lambda api_base, token, release_id, retry_fn=None: {
                "BOSS_ResumeFilter_mac.zip": {"id": 1, "size": 222},
                "BOSS_ResumeFilter.dmg": {"id": 2, "size": 333},
            }
            build._get_github_release_assets = lambda tag: {
                "BOSS_ResumeFilter_mac.zip": {
                    "name": "BOSS_ResumeFilter_mac.zip",
                    "size": 222,
                    "digest": "sha256:" + "b" * 64,
                },
                "BOSS_ResumeFilter.dmg": {
                    "name": "BOSS_ResumeFilter.dmg",
                    "size": 333,
                    "digest": "sha256:" + "c" * 64,
                },
            }
            build._download_from_github_release = lambda tag, name, dest: (_ for _ in ()).throw(
                AssertionError("reusable Gitee assets should avoid GitHub downloads")
            )

            with _with_build_context(tmp_path, dist_dir, is_win=True, is_mac=False):
                downloads_cn = build._sync_gitee_from_github(
                    "9.9.9", "title", "notes", need_wait=False, release_cache=release_cache
                )
        finally:
            build._gitee_fetch_assets = original_fetch_assets
            build._get_github_release_assets = original_get_assets
            build._download_from_github_release = original_download

    assert downloads_cn == {
        "macos": "https://gitee.com/owner/repo/releases/download/v9.9.9/BOSS_ResumeFilter_mac.zip",
        "macos_dmg": "https://gitee.com/owner/repo/releases/download/v9.9.9/BOSS_ResumeFilter.dmg",
    }


def test_sync_gitee_from_github_refreshes_stale_release_cache_before_upload():
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        dist_dir = tmp_path / "dist"
        dist_dir.mkdir()
        (tmp_path / "latest.json").write_text(
            json.dumps({
                "assets": {
                    "macos": {"size": 222, "sha256": "b" * 64},
                    "macos_dmg": {"size": 333, "sha256": "c" * 64},
                }
            }),
            encoding="utf-8",
        )
        release_cache = {
            "token": "token",
            "owner": "owner",
            "repo": "repo",
            "tag": "v9.9.9",
            "api_base": "https://gitee.example/api",
            "release_id": 1,
            "existing": {},
        }

        original_fetch_assets = build._gitee_fetch_assets
        original_get_assets = build._get_github_release_assets
        original_download = build._download_from_github_release
        original_upload = build._gitee_upload_single
        try:
            build._gitee_fetch_assets = lambda api_base, token, release_id, retry_fn=None: {
                "BOSS_ResumeFilter_mac.zip": {"id": 1, "size": 222},
                "BOSS_ResumeFilter.dmg": {"id": 2, "size": 333},
            }
            build._get_github_release_assets = lambda tag: {
                "BOSS_ResumeFilter_mac.zip": {
                    "name": "BOSS_ResumeFilter_mac.zip",
                    "size": 222,
                    "digest": "sha256:" + "b" * 64,
                },
                "BOSS_ResumeFilter.dmg": {
                    "name": "BOSS_ResumeFilter.dmg",
                    "size": 333,
                    "digest": "sha256:" + "c" * 64,
                },
            }
            build._download_from_github_release = lambda tag, name, dest: (_ for _ in ()).throw(
                AssertionError("fresh Gitee assets should avoid GitHub downloads")
            )
            build._gitee_upload_single = lambda path, api_base, token, release_id: (_ for _ in ()).throw(
                AssertionError("fresh Gitee assets should avoid duplicate uploads")
            )

            with _with_build_context(tmp_path, dist_dir, is_win=True, is_mac=False):
                downloads_cn = build._sync_gitee_from_github(
                    "9.9.9", "title", "notes", need_wait=False, release_cache=release_cache
                )
        finally:
            build._gitee_fetch_assets = original_fetch_assets
            build._get_github_release_assets = original_get_assets
            build._download_from_github_release = original_download
            build._gitee_upload_single = original_upload

    assert release_cache["existing"] == {
        "BOSS_ResumeFilter_mac.zip": {"id": 1, "size": 222},
        "BOSS_ResumeFilter.dmg": {"id": 2, "size": 333},
    }
    assert downloads_cn == {
        "macos": "https://gitee.com/owner/repo/releases/download/v9.9.9/BOSS_ResumeFilter_mac.zip",
        "macos_dmg": "https://gitee.com/owner/repo/releases/download/v9.9.9/BOSS_ResumeFilter.dmg",
    }


def test_gitee_clean_old_assets_dry_run_keeps_all_assets():
    releases = [
        {"tag_name": "v9.9.9", "id": 1},
        {"tag_name": "v9.9.8", "id": 2},
    ]
    assets_by_release = {
        1: {"BOSS_ResumeFilter.exe": {"id": 10, "size": 100}},
        2: {"BOSS_ResumeFilter.exe": {"id": 20, "size": 200}},
    }
    deleted = []

    original_env = build.os.environ.get("GITEE_TOKEN")
    original_ping = build._gitee_ping
    original_fetch_releases = build._gitee_fetch_releases
    original_fetch_assets = build._gitee_fetch_assets
    original_delete = build._gitee_delete_asset
    try:
        build.os.environ["GITEE_TOKEN"] = "token"
        build._gitee_ping = lambda token: True
        build._gitee_fetch_releases = lambda api_base, token: releases
        build._gitee_fetch_assets = lambda api_base, token, release_id, retry_fn=None: assets_by_release[release_id]
        build._gitee_delete_asset = lambda *args: deleted.append(args)

        ok = build._gitee_clean_old_assets("9.9.9", apply=False)
    finally:
        if original_env is None:
            build.os.environ.pop("GITEE_TOKEN", None)
        else:
            build.os.environ["GITEE_TOKEN"] = original_env
        build._gitee_ping = original_ping
        build._gitee_fetch_releases = original_fetch_releases
        build._gitee_fetch_assets = original_fetch_assets
        build._gitee_delete_asset = original_delete

    assert ok is True
    assert deleted == []


def test_gitee_release_lookup_uses_explicit_pagination():
    class FakeResponse:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return [{"id": 1, "tag_name": "v9.9.9", "name": "v9.9.9", "body": ""}]

    class FakeSession:
        def __init__(self):
            self.calls = []

        def get(self, url, **kwargs):
            self.calls.append((url, kwargs))
            return FakeResponse()

    session = FakeSession()
    original_session = build._gitee_session
    original_fetch_assets = build._gitee_fetch_assets
    try:
        build._gitee_session = lambda **_kwargs: session
        build._gitee_fetch_assets = lambda *_args, **_kwargs: {}
        release_id, assets = build._gitee_find_or_create_release(
            "https://gitee.com/api/v5/repos/owner/repo",
            "secret-token",
            "v9.9.9",
            "v9.9.9",
            "",
        )
    finally:
        build._gitee_session = original_session
        build._gitee_fetch_assets = original_fetch_assets

    assert release_id == 1
    assert assets == {}
    assert session.calls[0][1]["params"] == {
        "access_token": "secret-token",
        "page": 1,
        "per_page": 100,
    }


def test_gitee_release_create_accepts_lost_response_when_release_exists():
    class FakeResponse:
        def __init__(self, payload):
            self.payload = payload

        def raise_for_status(self):
            return None

        def json(self):
            return self.payload

    class FakeSession:
        def __init__(self):
            self.get_count = 0
            self.post_count = 0

        def get(self, _url, **_kwargs):
            self.get_count += 1
            payload = [] if self.get_count == 1 else [{
                "id": 7,
                "tag_name": "v9.9.9",
                "name": "v9.9.9",
                "body": "notes",
            }]
            return FakeResponse(payload)

        def post(self, _url, **_kwargs):
            self.post_count += 1
            raise build.requests.exceptions.ConnectionError("lost response")

    session = FakeSession()
    with patch.object(build, "_gitee_session", return_value=session):
        release_id, assets = build._gitee_find_or_create_release(
            "https://gitee.com/api/v5/repos/owner/repo",
            "secret-token",
            "v9.9.9",
            "v9.9.9",
            "notes",
        )

    assert release_id == 7
    assert assets == {}
    assert session.post_count == 1


def test_gitee_upload_accepts_lost_response_when_asset_is_complete():
    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return [{"id": 9, "name": "artifact.bin", "size": 4}]

    class FakeSession:
        def __init__(self):
            self.post_count = 0

        def post(self, _url, **_kwargs):
            self.post_count += 1
            raise build.requests.exceptions.ConnectionError("lost response")

        def get(self, _url, **_kwargs):
            return FakeResponse()

    with tempfile.TemporaryDirectory() as temp_dir:
        artifact = Path(temp_dir) / "artifact.bin"
        artifact.write_bytes(b"data")
        session = FakeSession()
        with patch.object(build, "_gitee_session", return_value=session):
            name, remote = build._gitee_upload_single(
                artifact,
                "https://gitee.com/api/v5/repos/owner/repo",
                "secret-token",
                7,
            )

    assert name == "artifact.bin"
    assert remote["size"] == 4
    assert session.post_count == 1


def test_gitee_upload_retry_and_final_error_never_log_access_token():
    secret = "gitee-secret-token"
    raw_error = build.requests.exceptions.ProxyError(
        "upload failed: /attach_files?access_token="
        f"{secret} (connection reset)"
    )

    class FakeSession:
        def post(self, _url, **_kwargs):
            raise raw_error

        def get(self, _url, **_kwargs):
            raise raw_error

    with tempfile.TemporaryDirectory() as temp_dir:
        artifact = Path(temp_dir) / "artifact.bin"
        artifact.write_bytes(b"data")
        output = io.StringIO()
        with (
            patch.object(build, "_gitee_session", return_value=FakeSession()),
            patch.object(build.time, "sleep"),
            contextlib.redirect_stdout(output),
        ):
            try:
                build._gitee_upload_single(
                    artifact,
                    "https://gitee.com/api/v5/repos/owner/repo",
                    secret,
                    7,
                    max_retries=1,
                )
            except build.requests.exceptions.RequestException as exc:
                final_error = str(exc)
            else:
                raise AssertionError("the failed upload must raise")

    combined = output.getvalue() + final_error
    assert secret not in combined
    assert "access_token=[REDACTED]" in combined
    assert "ProxyError" in combined
    assert "connection reset" in combined


def test_remote_ref_query_retries_three_transient_failures():
    failed = build.subprocess.CompletedProcess(
        ["git"], 1, stdout="", stderr="temporary network failure"
    )
    success = build.subprocess.CompletedProcess(
        ["git"],
        0,
        stdout=f"{'a' * 40}\trefs/heads/master\n",
        stderr="",
    )
    with (
        patch.object(
            build.subprocess,
            "run",
            side_effect=[failed, failed, failed, success],
        ) as run,
        patch.object(build.time, "sleep") as sleep,
    ):
        result = build._remote_ref_commit("origin", "refs/heads/master")

    assert result == "a" * 40
    assert run.call_count == 4
    assert sleep.call_args_list == [call(2), call(4), call(6)]


def test_gitee_clean_old_assets_apply_deletes_only_non_current_assets():
    releases = [
        {"tag_name": "v9.9.9", "id": 1},
        {"tag_name": "v9.9.8", "id": 2},
        {"tag_name": "v9.9.7", "id": 3},
    ]
    assets_by_release = {
        1: {"BOSS_ResumeFilter.exe": {"id": 10, "size": 100}},
        2: {"BOSS_ResumeFilter.exe": {"id": 20, "size": 200}},
        3: {"README.md": {"id": 30, "size": 300}},
    }
    deleted = []

    original_env = build.os.environ.get("GITEE_TOKEN")
    original_ping = build._gitee_ping
    original_fetch_releases = build._gitee_fetch_releases
    original_fetch_assets = build._gitee_fetch_assets
    original_delete = build._gitee_delete_asset
    try:
        build.os.environ["GITEE_TOKEN"] = "token"
        build._gitee_ping = lambda token: True
        build._gitee_fetch_releases = lambda api_base, token: releases
        build._gitee_fetch_assets = lambda api_base, token, release_id, retry_fn=None: assets_by_release[release_id]

        def fake_delete(api_base, token, release_id, asset_id, filename):
            deleted.append((release_id, asset_id, filename))

        build._gitee_delete_asset = fake_delete

        ok = build._gitee_clean_old_assets("v9.9.9", apply=True)
    finally:
        if original_env is None:
            build.os.environ.pop("GITEE_TOKEN", None)
        else:
            build.os.environ["GITEE_TOKEN"] = original_env
        build._gitee_ping = original_ping
        build._gitee_fetch_releases = original_fetch_releases
        build._gitee_fetch_assets = original_fetch_assets
        build._gitee_delete_asset = original_delete

    assert ok is True
    assert deleted == [
        (2, 20, "v9.9.8/BOSS_ResumeFilter.exe"),
        (3, 30, "v9.9.7/README.md"),
    ]


def test_sync_gitee_from_github_transfers_both_macos_assets():
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        dist_dir = tmp_path / "dist"
        dist_dir.mkdir()
        release_cache = {
            "token": "token",
            "owner": "owner",
            "repo": "repo",
            "tag": "v9.9.9",
            "api_base": "https://gitee.example/api",
            "release_id": 1,
            "existing": {},
        }
        download_order = []
        upload_order = []

        original_fetch_assets = build._gitee_fetch_assets
        original_get_assets = build._get_github_release_assets
        original_download = build._download_from_github_release
        original_upload = build._gitee_upload_single
        original_large_threshold = build.LARGE_TRANSFER_THRESHOLD
        try:
            build.LARGE_TRANSFER_THRESHOLD = 3
            build._gitee_fetch_assets = lambda api_base, token, release_id, retry_fn=None: {}
            build._get_github_release_assets = lambda tag: {
                "BOSS_ResumeFilter_mac.zip": {
                    "name": "BOSS_ResumeFilter_mac.zip",
                    "size": build.LARGE_TRANSFER_THRESHOLD + 1,
                    "digest": "sha256:" + "b" * 64,
                },
                "BOSS_ResumeFilter.dmg": {
                    "name": "BOSS_ResumeFilter.dmg",
                    "size": build.LARGE_TRANSFER_THRESHOLD + 2,
                    "digest": "sha256:" + "c" * 64,
                },
            }

            def fake_download(tag, name, dest_dir):
                download_order.append(name)
                path = Path(dest_dir) / name
                path.write_bytes(b"asset")
                return path

            def fake_upload(path, api_base, token, release_id):
                upload_order.append(path.name)
                return path.name, {}

            build._download_from_github_release = fake_download
            build._gitee_upload_single = fake_upload

            with _with_build_context(tmp_path, dist_dir, is_win=True, is_mac=False):
                downloads_cn = build._sync_gitee_from_github(
                    "9.9.9", "title", "notes", need_wait=False, release_cache=release_cache
                )
        finally:
            build._gitee_fetch_assets = original_fetch_assets
            build._get_github_release_assets = original_get_assets
            build._download_from_github_release = original_download
            build._gitee_upload_single = original_upload
            build.LARGE_TRANSFER_THRESHOLD = original_large_threshold

    assert sorted(download_order) == sorted(["BOSS_ResumeFilter_mac.zip", "BOSS_ResumeFilter.dmg"]), \
        f"download_order: {download_order}"
    assert sorted(upload_order) == sorted(["BOSS_ResumeFilter_mac.zip", "BOSS_ResumeFilter.dmg"]), \
        f"upload_order: {upload_order}"
    assert "macos" in downloads_cn, f"downloads_cn keys: {list(downloads_cn.keys()) if downloads_cn else 'None'}"
    assert downloads_cn["macos"].endswith("/BOSS_ResumeFilter_mac.zip"), \
        f"downloads_cn[macos]: {downloads_cn.get('macos')}"
    assert downloads_cn["macos_dmg"].endswith("/BOSS_ResumeFilter.dmg"), \
        f"downloads_cn[macos_dmg]: {downloads_cn.get('macos_dmg')}"


def test_sync_gitee_from_github_supports_macos_release_waiting_for_windows_exe():
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        dist_dir = tmp_path / "dist"
        dist_dir.mkdir()
        release_cache = {
            "token": "token",
            "owner": "owner",
            "repo": "repo",
            "tag": "v9.9.9",
            "api_base": "https://gitee.example/api",
            "release_id": 1,
            "existing": {},
        }
        download_order = []
        upload_order = []

        original_fetch_assets = build._gitee_fetch_assets
        original_get_assets = build._get_github_release_assets
        original_download = build._download_from_github_release
        original_upload = build._gitee_upload_single
        original_large_threshold = build.LARGE_TRANSFER_THRESHOLD
        try:
            build.LARGE_TRANSFER_THRESHOLD = 3
            build._gitee_fetch_assets = lambda api_base, token, release_id, retry_fn=None: {}
            build._get_github_release_assets = lambda tag: {
                "BOSS_ResumeFilter.exe": {
                    "name": "BOSS_ResumeFilter.exe",
                    "size": build.LARGE_TRANSFER_THRESHOLD + 1,
                    "digest": "sha256:" + "a" * 64,
                },
            }

            def fake_download(tag, name, dest_dir):
                download_order.append(name)
                path = Path(dest_dir) / name
                path.write_bytes(b"asset")
                return path

            def fake_upload(path, api_base, token, release_id):
                upload_order.append(path.name)
                return path.name, {}

            build._download_from_github_release = fake_download
            build._gitee_upload_single = fake_upload

            with _with_build_context(tmp_path, dist_dir, is_win=False, is_mac=True):
                downloads_cn = build._sync_gitee_from_github(
                    "9.9.9", "title", "notes", need_wait=False, release_cache=release_cache
                )
        finally:
            build._gitee_fetch_assets = original_fetch_assets
            build._get_github_release_assets = original_get_assets
            build._download_from_github_release = original_download
            build._gitee_upload_single = original_upload
            build.LARGE_TRANSFER_THRESHOLD = original_large_threshold

    assert download_order == ["BOSS_ResumeFilter.exe"]
    assert upload_order == ["BOSS_ResumeFilter.exe"]
    assert downloads_cn == {
        "windows": "https://gitee.com/owner/repo/releases/download/v9.9.9/BOSS_ResumeFilter.exe"
    }


def test_transfer_batch_runs_small_files_before_large_files():
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        small = tmp_path / "README.md"
        zip_path = tmp_path / "BOSS_ResumeFilter_mac.zip"
        dmg_path = tmp_path / "BOSS_ResumeFilter.dmg"
        small.write_bytes(b"x")
        zip_path.write_bytes(b"large")
        dmg_path.write_bytes(b"large")
        order = []

        original_large_threshold = build.LARGE_TRANSFER_THRESHOLD
        try:
            build.LARGE_TRANSFER_THRESHOLD = 3

            def worker(path):
                order.append(path.name)
                return path.name

            build._run_transfer_batch(
                [small, zip_path, dmg_path],
                "测试传输",
                worker,
                lambda item, result: None,
                lambda item, error: None,
            )
        finally:
            build.LARGE_TRANSFER_THRESHOLD = original_large_threshold

    assert order[0] == "README.md"
    assert order[1:] == ["BOSS_ResumeFilter_mac.zip", "BOSS_ResumeFilter.dmg"]


def test_transfer_batch_can_upload_two_large_files_concurrently():
    import threading

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        zip_path = tmp_path / "BOSS_ResumeFilter_mac.zip"
        dmg_path = tmp_path / "BOSS_ResumeFilter.dmg"
        zip_path.write_bytes(b"large")
        dmg_path.write_bytes(b"large")
        barrier = threading.Barrier(2)
        lock = threading.Lock()
        active = 0
        max_active = 0

        original_large_threshold = build.LARGE_TRANSFER_THRESHOLD
        try:
            build.LARGE_TRANSFER_THRESHOLD = 1

            def worker(path):
                nonlocal active, max_active
                with lock:
                    active += 1
                    max_active = max(max_active, active)
                barrier.wait(timeout=2)
                with lock:
                    active -= 1
                return path.name

            build._run_transfer_batch(
                [zip_path, dmg_path],
                "测试并发传输",
                worker,
                lambda item, result: None,
                lambda item, error: (_ for _ in ()).throw(error),
                large_workers=2,
            )
        finally:
            build.LARGE_TRANSFER_THRESHOLD = original_large_threshold

    assert max_active == 2


def test_update_latest_json_requires_complete_auto_update_metadata():
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        dist_dir = tmp_path / "dist"
        dist_dir.mkdir()
        (dist_dir / "BOSS_ResumeFilter.exe").write_bytes(b"exe")

        with _with_build_context(tmp_path, dist_dir, is_win=True, is_mac=False):
            with contextlib.redirect_stdout(io.StringIO()):
                try:
                    build.update_latest_json("9.9.9", "notes", quiet=True, require_complete_assets=True)
                except SystemExit as exc:
                    assert exc.code == 1
                else:
                    raise AssertionError("missing macos metadata should block latest.json publication")


def test_readme_release_detail_mismatch_is_warning_by_default():
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        dist_dir = tmp_path / "dist"
        dist_dir.mkdir()
        (tmp_path / "CHANGELOG.md").write_text(
            "\n".join([
                "## v9.9.9 — 测试版本",
                "",
                "### 新增功能",
                "- **功能 A**：说明",
                "- **功能 B**：说明",
                "",
                "### 体验优化",
                "- **优化 A**：说明",
                "",
                "### 问题修复",
                "- **修复 A**：说明",
            ]),
            encoding="utf-8",
        )
        (tmp_path / "README.md").write_text(
            "\n".join([
                "> 当前发布版本：v9.9.9 测试版本",
                "",
                "### v9.9.9 测试版本",
                "",
                "**新增功能**",
                "- **功能 A**：摘要",
                "",
                "**体验优化**",
                "- **优化 A**：摘要",
                "",
                "**问题修复**",
                "- **修复 A**：摘要",
                "",
                "├── gui_main.py            # 图形界面主程序（v9.9.9）",
            ]),
            encoding="utf-8",
        )

        with _with_build_context(tmp_path, dist_dir, is_win=True, is_mac=False):
            with contextlib.redirect_stdout(io.StringIO()):
                build._check_readme_release("9.9.9", strict_details=False)
            with contextlib.redirect_stdout(io.StringIO()):
                try:
                    build._check_readme_release("9.9.9", strict_details=True)
                except SystemExit as exc:
                    assert exc.code == 1
                else:
                    raise AssertionError("strict README detail check should fail on title/count mismatch")


def test_latest_json_release_notes_mismatch_is_warning_by_default():
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        dist_dir = tmp_path / "dist"
        dist_dir.mkdir()
        (tmp_path / "CHANGELOG.md").write_text(
            "\n".join([
                "## v9.9.9 — 测试版本",
                "",
                "### 新增功能",
                "- **功能 A**：说明",
                "",
                "### 体验优化",
                "- **优化 A**：说明",
                "",
                "### 问题修复",
                "- **修复 A**：说明",
            ]),
            encoding="utf-8",
        )
        (tmp_path / "latest.json").write_text(
            json.dumps({"version": "9.9.9", "release_notes": "stale"}, ensure_ascii=False),
            encoding="utf-8",
        )

        with _with_build_context(tmp_path, dist_dir, is_win=True, is_mac=False):
            with contextlib.redirect_stdout(io.StringIO()):
                build._check_latest_json_release_notes("9.9.9", strict=False)
            with contextlib.redirect_stdout(io.StringIO()):
                try:
                    build._check_latest_json_release_notes("9.9.9", strict=True)
                except SystemExit as exc:
                    assert exc.code == 1
                else:
                    raise AssertionError("strict latest.json release notes check should fail")


def test_update_latest_json_skips_when_content_is_unchanged():
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        dist_dir = tmp_path / "dist"
        dist_dir.mkdir()
        exe_path = dist_dir / "BOSS_ResumeFilter.exe"
        exe_path.write_bytes(b"exe")

        with _with_build_context(tmp_path, dist_dir, is_win=True, is_mac=False):
            changed_first = build.update_latest_json("9.9.9", "notes", quiet=True)
            before = (tmp_path / "latest.json").read_text(encoding="utf-8")
            changed_second = build.update_latest_json("9.9.9", "notes", quiet=True)
            after = (tmp_path / "latest.json").read_text(encoding="utf-8")

    assert changed_first is True
    assert changed_second is False
    assert after == before


def test_update_latest_json_preserves_existing_release_date_for_same_version():
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        dist_dir = tmp_path / "dist"
        dist_dir.mkdir()
        (dist_dir / "BOSS_ResumeFilter.exe").write_bytes(b"exe")
        existing = {
            "version": "9.9.9",
            "release_date": "2026-01-02",
            "downloads": {},
            "assets": {},
            "release_notes": "old",
        }
        (tmp_path / "latest.json").write_text(json.dumps(existing), encoding="utf-8")

        with _with_build_context(tmp_path, dist_dir, is_win=True, is_mac=False):
            build.update_latest_json("9.9.9", "notes", quiet=True)
            data = json.loads((tmp_path / "latest.json").read_text(encoding="utf-8"))

    assert data["release_date"] == "2026-01-02"


def test_update_latest_json_preserves_same_version_other_platform_assets():
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        dist_dir = tmp_path / "dist"
        dist_dir.mkdir()
        exe_path = dist_dir / "BOSS_ResumeFilter.exe"
        exe_path.write_bytes(b"exe")
        expected_exe_sha256 = build._sha256_file(exe_path)
        existing = {
            "version": "9.9.9",
            "release_date": "2026-01-02",
            "downloads": {},
            "assets": {
                "macos": {"size": 222, "sha256": "b" * 64},
                "macos_dmg": {"size": 333, "sha256": "c" * 64},
            },
            "release_notes": "old",
        }
        (tmp_path / "latest.json").write_text(json.dumps(existing), encoding="utf-8")

        with _with_build_context(tmp_path, dist_dir, is_win=True, is_mac=False):
            build.update_latest_json("9.9.9", "notes", quiet=True)
            data = json.loads((tmp_path / "latest.json").read_text(encoding="utf-8"))

    assert data["assets"]["windows"]["size"] == 3
    assert data["assets"]["windows"]["sha256"] == expected_exe_sha256
    assert data["assets"]["macos"] == {"size": 222, "sha256": "b" * 64}
    assert data["assets"]["macos_dmg"] == {"size": 333, "sha256": "c" * 64}


def test_release_version_rules_reject_zero_patch_tags():
    assert build._is_valid_release_tag("v2.9") is True
    assert build._is_valid_release_tag("v2.9.1") is True
    assert build._is_valid_release_tag("v2.9.0") is False
    assert build._is_valid_release_tag("2.9.1") is False


def test_release_tag_guard_allows_same_commit_resume():
    head = "a" * 40
    original_local_ref = build._local_ref_commit
    original_remote_tag = build._remote_tag_commit
    try:
        build._local_ref_commit = lambda ref: head
        build._remote_tag_commit = lambda remote, tag: head
        build._assert_release_tag_reusable("9.9.9")
    finally:
        build._local_ref_commit = original_local_ref
        build._remote_tag_commit = original_remote_tag


def test_release_tag_guard_rejects_different_remote_commit():
    head = "a" * 40
    original_local_ref = build._local_ref_commit
    original_remote_tag = build._remote_tag_commit
    try:
        build._local_ref_commit = lambda ref: head if ref == "HEAD" else None
        build._remote_tag_commit = lambda remote, tag: "b" * 40
        try:
            build._assert_release_tag_reusable("9.9.9")
        except SystemExit as exc:
            assert exc.code == 1
        else:
            raise AssertionError("conflicting remote tag should stop the release")
    finally:
        build._local_ref_commit = original_local_ref
        build._remote_tag_commit = original_remote_tag


def test_release_tag_guard_rejects_reuse_when_commit_will_change():
    head = "a" * 40
    original_local_ref = build._local_ref_commit
    original_remote_tag = build._remote_tag_commit
    try:
        build._local_ref_commit = lambda ref: head
        build._remote_tag_commit = lambda remote, tag: head
        try:
            build._assert_release_tag_reusable("9.9.9", will_create_commit=True)
        except SystemExit as exc:
            assert exc.code == 1
        else:
            raise AssertionError("an existing tag cannot be reused for a new commit")
    finally:
        build._local_ref_commit = original_local_ref
        build._remote_tag_commit = original_remote_tag


def test_git_tag_never_force_moves_existing_tag():
    original_local_ref = build._local_ref_commit
    original_run = build.subprocess.run
    calls = []
    try:
        build._local_ref_commit = lambda ref: "a" * 40 if ref == "HEAD" else "b" * 40
        build.subprocess.run = lambda args, **kwargs: calls.append(args)
        try:
            build._git_tag("9.9.9")
        except SystemExit as exc:
            assert exc.code == 1
        else:
            raise AssertionError("_git_tag should reject a conflicting local tag")
    finally:
        build._local_ref_commit = original_local_ref
        build.subprocess.run = original_run

    assert calls == []


def test_git_push_rejects_remote_tag_before_pushing_master():
    original_local_ref = build._local_ref_commit
    original_remote_tag = build._remote_tag_commit
    original_run = build.subprocess.run
    calls = []
    try:
        build._local_ref_commit = lambda ref: "a" * 40
        build._remote_tag_commit = lambda remote, tag: "b" * 40
        build.subprocess.run = lambda args, **kwargs: calls.append(args)
        try:
            build._git_push("9.9.9", auto=True)
        except SystemExit as exc:
            assert exc.code == 1
        else:
            raise AssertionError("_git_push should reject a conflicting remote tag")
    finally:
        build._local_ref_commit = original_local_ref
        build._remote_tag_commit = original_remote_tag
        build.subprocess.run = original_run

    assert calls == []


def test_git_push_skips_same_remote_tag_without_force():
    original_local_ref = build._local_ref_commit
    original_remote_tag = build._remote_tag_commit
    original_run = build.subprocess.run
    calls = []

    class Result:
        returncode = 0
        stdout = ""
        stderr = ""

    try:
        build._local_ref_commit = lambda ref: "a" * 40
        build._remote_tag_commit = lambda remote, tag: "a" * 40
        build.subprocess.run = lambda args, **kwargs: calls.append(args) or Result()
        build._git_push("9.9.9", auto=True)
    finally:
        build._local_ref_commit = original_local_ref
        build._remote_tag_commit = original_remote_tag
        build.subprocess.run = original_run

    assert calls == [["git", "push", "origin", "master"]]


def test_verify_latest_manifest_matches_public_release_metadata():
    version = "9.9.9"
    notes = "### 新增功能\n\n- test"
    github_assets = {
        "BOSS_ResumeFilter.exe": {"size": 111, "digest": "sha256:" + "a" * 64},
        "BOSS_ResumeFilter_mac.zip": {"size": 222, "digest": "sha256:" + "b" * 64},
        "BOSS_ResumeFilter.dmg": {"size": 333, "digest": "sha256:" + "c" * 64},
    }
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        dist_dir = tmp_path / "dist"
        dist_dir.mkdir()
        latest = {
            "version": version,
            "downloads": {
                "windows": f"https://github.com/yaoyouzhong/boss-resume-filter/releases/download/v{version}/BOSS_ResumeFilter.exe",
                "macos": f"https://github.com/yaoyouzhong/boss-resume-filter/releases/download/v{version}/BOSS_ResumeFilter_mac.zip",
                "macos_dmg": f"https://github.com/yaoyouzhong/boss-resume-filter/releases/download/v{version}/BOSS_ResumeFilter.dmg",
            },
            "downloads_cn": {
                "windows": f"https://gitee.com/yaoyouzhong/boss-resume-filter/releases/download/v{version}/BOSS_ResumeFilter.exe",
                "macos": f"https://gitee.com/yaoyouzhong/boss-resume-filter/releases/download/v{version}/BOSS_ResumeFilter_mac.zip",
                "macos_dmg": f"https://gitee.com/yaoyouzhong/boss-resume-filter/releases/download/v{version}/BOSS_ResumeFilter.dmg",
            },
            "assets": {
                "windows": {"size": 111, "sha256": "a" * 64},
                "macos": {"size": 222, "sha256": "b" * 64},
                "macos_dmg": {"size": 333, "sha256": "c" * 64},
            },
            "release_notes": notes,
        }
        (tmp_path / "latest.json").write_text(json.dumps(latest), encoding="utf-8")

        with _with_build_context(tmp_path, dist_dir, is_win=True, is_mac=False):
            assert build._verify_latest_manifest(version, github_assets, notes) is True
            latest["assets"]["windows"]["sha256"] = "0" * 64
            (tmp_path / "latest.json").write_text(json.dumps(latest), encoding="utf-8")
            assert build._verify_latest_manifest(version, github_assets, notes) is False


def test_version_history_integrity_ignores_invalid_zero_patch_local_tag():
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        dist_dir = tmp_path / "dist"
        dist_dir.mkdir()
        (tmp_path / "CHANGELOG.md").write_text(
            "\n".join([
                "## v2.10.1",
                "- current",
                "## v2.9.1",
                "- patch",
                "## v2.9",
                "- major",
            ]),
            encoding="utf-8",
        )
        (tmp_path / "README.md").write_text(
            "\n".join([
                "### v2.10.1",
                "- current",
                "### v2.9.1",
                "- patch",
                "### v2.9",
                "- major",
            ]),
            encoding="utf-8",
        )
        (tmp_path / "gui_main.py").write_text('__version__ = "2.10.1"', encoding="utf-8")

        original_run = build.subprocess.run

        def fake_run(args, **kwargs):
            if args[:4] == ["git", "tag", "-l", "v*"]:
                class Result:
                    returncode = 0
                    stdout = "v2.10.1\nv2.9.0\nv2.9.1\nv2.9\n"
                return Result()
            return original_run(args, **kwargs)

        with _with_build_context(tmp_path, dist_dir, is_win=True, is_mac=False):
            build.subprocess.run = fake_run
            try:
                build._check_version_history_integrity()
            finally:
                build.subprocess.run = original_run


def test_version_history_integrity_includes_untagged_current_version():
    """发布前的新版本尚无 tag，README 最近三版仍应以源码版本为首。"""
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        dist_dir = tmp_path / "dist"
        dist_dir.mkdir()
        (tmp_path / "gui_main.py").write_text('__version__ = "2.11"', encoding="utf-8")
        (tmp_path / "CHANGELOG.md").write_text(
            "\n".join([
                "## v2.11",
                "- current",
                "## v2.10.1",
                "- patch",
                "## v2.9.1",
                "- patch",
                "## v2.9",
                "- major",
            ]),
            encoding="utf-8",
        )
        (tmp_path / "README.md").write_text(
            "\n".join([
                "### v2.11",
                "- current",
                "### v2.10.1",
                "- patch",
                "### v2.9.1",
                "- patch",
                "### v2.9 及更早版本",
            ]),
            encoding="utf-8",
        )

        original_run = build.subprocess.run

        def fake_run(args, **kwargs):
            if args[:4] == ["git", "tag", "-l", "v*"]:
                class Result:
                    returncode = 0
                    stdout = "v2.10.1\nv2.9.1\nv2.9\n"
                return Result()
            return original_run(args, **kwargs)

        with _with_build_context(tmp_path, dist_dir, is_win=True, is_mac=False):
            build.subprocess.run = fake_run
            try:
                build._check_version_history_integrity()
            finally:
                build.subprocess.run = original_run


def test_needs_local_rebuild_uses_build_fingerprint():
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        dist_dir = tmp_path / "dist"
        dist_dir.mkdir()
        (tmp_path / "gui_main.py").write_text('__version__ = "9.9.9"', encoding="utf-8")
        (tmp_path / "build.py").write_text("build script", encoding="utf-8")
        (tmp_path / "requirements.txt").write_text("requests>=2", encoding="utf-8")
        exe_path = dist_dir / "BOSS_ResumeFilter.exe"
        exe_path.write_bytes(b"exe")

        cmd = ["python", "-m", "PyInstaller", "gui_main.py"]
        with _with_build_context(tmp_path, dist_dir, is_win=True, is_mac=False):
            fingerprint = build._build_fingerprint(cmd)
            build._write_build_state(fingerprint)
            needs, reason = build._needs_local_rebuild(cmd)
            (tmp_path / "gui_main.py").write_text("__version__ = \"9.9.10\"", encoding="utf-8")
            needs_after_change, reason_after_change = build._needs_local_rebuild(cmd)

    assert needs is False
    assert "未变化" in reason
    assert needs_after_change is True
    assert "指纹变化" in reason_after_change


def test_cross_platform_rebuild_policy_distinguishes_build_and_docs_changes():
    assert build._needs_cross_platform_rebuild(["build.py"]) is True
    assert build._needs_cross_platform_rebuild(["pyinstaller-hooks/hook-babel.py"]) is True
    assert build._needs_cross_platform_rebuild(["README.md", "CHANGELOG.md"]) is False
    assert build._needs_cross_platform_rebuild(["tests/unit/test_update_integrity.py"]) is False


def test_project_docs_version_sync_uses_agents_as_single_source():
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        dist_dir = tmp_path / "dist"
        dist_dir.mkdir()
        (tmp_path / "AGENTS.md").write_text(
            "├── gui_main.py            # 图形界面主程序（v9.9.9）\n",
            encoding="utf-8",
        )
        (tmp_path / "CLAUDE.md").write_text(
            "# Claude Code 入口\n\n@AGENTS.md\n",
            encoding="utf-8",
        )

        with _with_build_context(tmp_path, dist_dir, is_win=True, is_mac=False):
            build._check_project_docs_version_sync("9.9.9")


def test_all_root_python_sources_trigger_cross_platform_rebuild():
    root_sources = sorted(path.name for path in build.BASE_DIR.glob("*.py"))

    assert root_sources
    assert set(root_sources) == set(build.ROOT_PYTHON_SOURCE_FILES)
    assert all(
        build._needs_cross_platform_rebuild([source]) is True
        for source in root_sources
    )


def test_unknown_production_path_conservatively_triggers_cross_platform_rebuild():
    assert build._needs_cross_platform_rebuild(["future_runtime/feature.py"]) is True


def test_source_compile_gate_includes_every_root_python_module():
    root_sources = {path.name for path in build.BASE_DIR.glob("*.py")}

    assert root_sources <= set(build.SOURCE_CHECK_FILES)


def test_github_asset_matches_local_by_digest_without_download():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "asset.exe"
        path.write_bytes(b"MZsame-content")
        asset = {
            "size": path.stat().st_size,
            "digest": f"sha256:{build._sha256_file(path)}",
        }

        same, reason = build._github_asset_matches_local("v9.9.9", path, asset)

    assert same is True
    assert "SHA256 一致" in reason


def test_github_asset_size_mismatch_requires_upload():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "asset.exe"
        path.write_bytes(b"MZlocal")
        asset = {"size": path.stat().st_size + 1}

        same, reason = build._github_asset_matches_local("v9.9.9", path, asset)

    assert same is False
    assert "大小不一致" in reason


def test_gitee_asset_matches_local_downloads_remote_hash_when_digest_missing():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "asset.exe"
        path.write_bytes(b"MZsame-content")
        original_remote_hash = build._remote_file_sha256
        calls = []

        def fake_remote_hash(url, token=None):
            calls.append((url, token))
            return build._sha256_file(path)

        build._remote_file_sha256 = fake_remote_hash
        try:
            same, reason = build._gitee_asset_matches_local(
                path,
                {"size": path.stat().st_size},
                "owner",
                "repo",
                "v9.9.9",
                token="token",
            )
        finally:
            build._remote_file_sha256 = original_remote_hash

    assert same is True
    assert "SHA256 一致" in reason
    assert calls == [("https://gitee.com/owner/repo/releases/download/v9.9.9/asset.exe", "token")]


def test_gitee_asset_matches_local_uses_latest_json_metadata_before_download():
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        dist_dir = tmp_path / "dist"
        dist_dir.mkdir()
        path = dist_dir / "BOSS_ResumeFilter.exe"
        path.write_bytes(b"MZsame-content")
        latest = {
            "version": "9.9.9",
            "release_date": "2026-01-02",
            "downloads": {},
            "assets": {
                "windows": {
                    "size": path.stat().st_size,
                    "sha256": build._sha256_file(path),
                }
            },
            "release_notes": "notes",
        }
        (tmp_path / "latest.json").write_text(json.dumps(latest), encoding="utf-8")
        original_remote_hash = build._remote_file_sha256

        def fail_remote_hash(url, token=None):
            raise AssertionError("remote download should be skipped")

        with _with_build_context(tmp_path, dist_dir, is_win=True, is_mac=False):
            build._remote_file_sha256 = fail_remote_hash
            try:
                same, reason = build._gitee_asset_matches_local(
                    path,
                    {"size": path.stat().st_size},
                    "owner",
                    "repo",
                    "v9.9.9",
                    token="token",
                )
            finally:
                build._remote_file_sha256 = original_remote_hash

    assert same is True
    assert "latest.json 元数据一致" in reason


def test_current_platform_update_artifact_names_windows():
    original_is_mac = build.IS_MAC
    try:
        build.IS_MAC = False
        assert build._current_platform_update_artifact_names() == {"BOSS_ResumeFilter.exe"}
    finally:
        build.IS_MAC = original_is_mac


def test_current_platform_update_artifact_names_macos():
    original_is_mac = build.IS_MAC
    try:
        build.IS_MAC = True
        assert build._current_platform_update_artifact_names() == {
            "BOSS_ResumeFilter.dmg",
            "BOSS_ResumeFilter_mac.zip",
        }
    finally:
        build.IS_MAC = original_is_mac


def test_windows_update_helper_receives_verified_payload_and_clean_runtime_env():
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        current_exe = tmp_path / "BOSS_ResumeFilter.exe"
        new_exe = tmp_path / "download" / "BOSS_ResumeFilter.exe"
        new_exe.parent.mkdir()
        current_exe.write_bytes(b"old")
        new_exe.write_bytes(b"new")
        captured = {}

        class FakeProcess:
            def poll(self):
                return None

        def fake_popen(args, **kwargs):
            captured["args"] = args
            captured["kwargs"] = kwargs
            ready_path = Path(args[2]).parent / "update_helper.ready"
            ready_path.write_text("ready", encoding="utf-8")
            return FakeProcess()

        with (
            patch.dict(updater.os.environ, {"_PYI_TEST": "stale"}, clear=False),
            patch.object(updater.subprocess, "Popen", side_effect=fake_popen),
        ):
            ok, error = updater.update_windows(
                str(new_exe),
                str(current_exe),
                source="startup",
                asset_info={"size": 3, "sha256": "a" * 64},
                old_version="2.25.1",
            )
        payload = json.loads(
            (new_exe.parent / "update_payload.json").read_text(encoding="utf-8")
        )

    assert ok is True
    assert error is None
    assert Path(captured["args"][0]).name == "BOSS_ResumeFilter_updater.exe"
    assert captured["args"][1] == "--apply-windows-update"
    assert captured["kwargs"]["env"]["PYINSTALLER_RESET_ENVIRONMENT"] == "1"
    assert "_PYI_TEST" not in captured["kwargs"]["env"]
    assert payload["source"] == "startup"
    assert payload["old_version"] == "2.25.1"
    assert payload["asset_info"] == {"size": 3, "sha256": "a" * 64}
    assert payload["old_exe"] == str(current_exe.resolve())
    assert payload["new_exe"] == str(new_exe.resolve())


def test_windows_backup_name_uses_the_running_old_version():
    exe = Path("C:/Apps/BOSS_ResumeFilter.exe")

    assert updater._versioned_backup_path(
        exe, "v2.25.1"
    ) == Path("C:/Apps/BOSS_ResumeFilter.exe.2.25.1")

    for invalid in ("", "../2.25.1", "2.25.1-beta", "unknown"):
        try:
            updater._versioned_backup_path(exe, invalid)
        except ValueError:
            pass
        else:
            raise AssertionError(f"invalid backup version was accepted: {invalid}")


def test_windows_update_cache_reuses_only_a_fully_verified_package():
    with tempfile.TemporaryDirectory() as tmp:
        with patch.object(updater, "get_base_dir", return_value=Path(tmp)):
            cached_exe = updater._windows_update_cache_path("2.26")
            assert cached_exe == (
                Path(tmp).resolve()
                / "updates"
                / "2.26"
                / "BOSS_ResumeFilter_new.exe"
            )
            cached_exe.parent.mkdir(parents=True)
            cached_exe.write_bytes(b"MZcached-update")
            result = {
                "latest": "2.26",
                "asset_info": {
                    "size": cached_exe.stat().st_size,
                    "sha256": updater._file_sha256(cached_exe),
                },
            }

            assert updater._get_cached_windows_update(result) == cached_exe

            result["asset_info"]["sha256"] = "0" * 64
            assert updater._get_cached_windows_update(result) is None
            assert cached_exe.exists()


def test_update_check_marks_a_verified_windows_cache_for_immediate_reuse():
    class ImmediateThread:
        def __init__(self, target, daemon):
            self.target = target

        def start(self):
            self.target()

    class ImmediateRoot:
        def after(self, _delay, callback):
            callback()

    with tempfile.TemporaryDirectory() as tmp:
        with patch.object(updater, "get_base_dir", return_value=Path(tmp)):
            cached_exe = updater._windows_update_cache_path("2.26")
            cached_exe.parent.mkdir(parents=True)
            cached_exe.write_bytes(b"MZcached-update")
            result = {
                "latest": "2.26",
                "current": "2.25.1",
                "has_update": True,
                "update_type": "version",
                "content_changed": False,
                "release_info": {"body": "notes"},
                "download_url": "https://example.test/update.exe",
                "download_url_fallback": None,
                "asset_info": {
                    "size": cached_exe.stat().st_size,
                    "sha256": updater._file_sha256(cached_exe),
                },
                "error": None,
            }

            with (
                patch.object(updater.sys, "platform", "win32"),
                patch.object(updater, "check_gitee_latest", return_value=result),
                patch.object(updater, "_fetch_changelog_section", return_value=None),
                patch.object(updater.threading, "Thread", ImmediateThread),
                patch.object(updater, "show_update_dialog") as show_dialog,
            ):
                updater.check_and_update_gui(ImmediateRoot(), silent=True)

    shown_result = show_dialog.call_args.args[1]
    assert shown_result["cached_update_path"] == str(cached_exe)


def test_windows_update_cleanup_accepts_only_managed_update_directories():
    with tempfile.TemporaryDirectory() as tmp:
        app_dir = Path(tmp) / "app"
        system_temp = Path(tmp) / "temp"
        with patch.object(
            updater.tempfile,
            "gettempdir",
            return_value=str(system_temp),
        ):
            temp_dir = system_temp / "boss_update_download_123"
            outsider = Path(tmp) / "unrelated"

            cache_dir = updater._windows_update_cache_dir("2.26", app_dir)
            assert updater._is_managed_windows_update_dir(cache_dir, app_dir) is True
            # 旧版系统临时目录不再由更新助手管理，必须拒绝清理
            assert updater._is_managed_windows_update_dir(temp_dir, app_dir) is False
            assert updater._is_managed_windows_update_dir(outsider, app_dir) is False


def test_visible_windows_update_reports_stages_and_keeps_previous_version():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        old_exe = root / "BOSS_ResumeFilter.exe"
        new_exe = root / "download" / "BOSS_ResumeFilter.exe"
        new_exe.parent.mkdir()
        old_exe.write_bytes(b"MZold-version")
        new_exe.write_bytes(b"MZnew-version")
        marker = root / "BOSS_ResumeFilter.exe.update_ok"
        progress = []
        payload = {
            "old_exe": str(old_exe),
            "new_exe": str(new_exe),
            "marker_path": str(marker),
            "old_pid": 123,
            "source": "manual",
            "old_version": "2.25.1",
            "asset_info": {
                "size": new_exe.stat().st_size,
                "sha256": updater._file_sha256(new_exe),
            },
        }
        stale_backup = Path(str(old_exe) + ".2.24.9")
        legacy_backup = Path(str(old_exe) + ".old")
        stale_backup.write_bytes(b"MZstale-version")
        legacy_backup.write_bytes(b"MZlegacy-version")
        process = Mock()
        process.poll.return_value = None

        with (
            patch.object(updater, "_wait_for_windows_process_exit", return_value=True),
            patch.object(updater, "_launch_updated_windows_app", return_value=process),
            patch.object(updater, "_wait_for_file", return_value=True),
        ):
            success, error = updater._apply_windows_update(
                payload,
                lambda percent, stage, detail: progress.append(
                    (percent, stage, detail)
                ),
            )

        assert success is True
        assert error is None
        assert old_exe.read_bytes() == b"MZnew-version"
        assert Path(str(old_exe) + ".2.25.1").read_bytes() == b"MZold-version"
        assert not stale_backup.exists()
        assert not legacy_backup.exists()
        assert [item[0] for item in progress] == [8, 20, 35, 55, 75, 88, 100]
        assert progress[-1][1] == "更新安装完成"


def test_visible_windows_update_rolls_back_and_reopens_original_on_install_error():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        old_exe = root / "BOSS_ResumeFilter.exe"
        new_exe = root / "download" / "BOSS_ResumeFilter.exe"
        new_exe.parent.mkdir()
        old_exe.write_bytes(b"MZold-version")
        new_exe.write_bytes(b"MZnew-version")
        existing_backup = Path(str(old_exe) + ".2.25.1")
        existing_backup.write_bytes(b"MZprior-backup")
        payload = {
            "old_exe": str(old_exe),
            "new_exe": str(new_exe),
            "marker_path": str(root / "BOSS_ResumeFilter.exe.update_ok"),
            "old_pid": 123,
            "source": "manual",
            "old_version": "2.25.1",
            "asset_info": {
                "size": new_exe.stat().st_size,
                "sha256": updater._file_sha256(new_exe),
            },
        }

        with (
            patch.object(updater, "_wait_for_windows_process_exit", return_value=True),
            patch.object(updater.shutil, "copy2", side_effect=OSError("copy failed")),
            patch.object(updater.subprocess, "Popen", return_value=Mock()) as popen,
        ):
            success, error = updater._apply_windows_update(
                payload,
                lambda *_args: None,
            )

        assert success is False
        assert "copy failed" in error
        assert old_exe.read_bytes() == b"MZold-version"
        assert existing_backup.read_bytes() == b"MZprior-backup"
        assert not Path(str(existing_backup) + ".previous").exists()
        assert Path(str(old_exe) + ".update_failed.txt").exists()
        popen.assert_called_once()


def test_current_exe_sha256_returns_none_in_source_mode():
    original_frozen = getattr(updater.sys, "frozen", None)
    original_cache = updater._current_exe_sha256_cache
    try:
        if hasattr(updater.sys, "frozen"):
            delattr(updater.sys, "frozen")
        updater._current_exe_sha256_cache = None

        assert updater._get_current_exe_sha256() is None
    finally:
        if original_frozen is not None:
            updater.sys.frozen = original_frozen
        elif hasattr(updater.sys, "frozen"):
            delattr(updater.sys, "frozen")
        updater._current_exe_sha256_cache = original_cache


def test_current_exe_sha256_hashes_windows_frozen_exe():
    with tempfile.TemporaryDirectory() as tmp:
        exe_path = Path(tmp) / "BOSS_ResumeFilter.exe"
        exe_path.write_bytes(b"MZboss-app")

        original_frozen = getattr(updater.sys, "frozen", None)
        original_platform = updater.sys.platform
        original_executable = updater.sys.executable
        original_cache = updater._current_exe_sha256_cache
        try:
            updater.sys.frozen = True
            updater.sys.platform = "win32"
            updater.sys.executable = str(exe_path)
            updater._current_exe_sha256_cache = None

            assert updater._get_current_exe_sha256() == updater._file_sha256(exe_path)
        finally:
            if original_frozen is not None:
                updater.sys.frozen = original_frozen
            elif hasattr(updater.sys, "frozen"):
                delattr(updater.sys, "frozen")
            updater.sys.platform = original_platform
            updater.sys.executable = original_executable
            updater._current_exe_sha256_cache = original_cache


def test_current_exe_sha256_hashes_macos_frozen_executable():
    with tempfile.TemporaryDirectory() as tmp:
        exe_path = Path(tmp) / "BOSS_ResumeFilter.app" / "Contents" / "MacOS" / "BOSS_ResumeFilter"
        exe_path.parent.mkdir(parents=True)
        exe_path.write_bytes(b"boss-macos-app")

        original_frozen = getattr(updater.sys, "frozen", None)
        original_platform = updater.sys.platform
        original_executable = updater.sys.executable
        original_cache = updater._current_exe_sha256_cache
        try:
            updater.sys.frozen = True
            updater.sys.platform = "darwin"
            updater.sys.executable = str(exe_path)
            updater._current_exe_sha256_cache = None

            assert updater._get_current_exe_sha256() == updater._file_sha256(exe_path)
        finally:
            if original_frozen is not None:
                updater.sys.frozen = original_frozen
            elif hasattr(updater.sys, "frozen"):
                delattr(updater.sys, "frozen")
            updater.sys.platform = original_platform
            updater.sys.executable = original_executable
            updater._current_exe_sha256_cache = original_cache
