# -*- coding: utf-8 -*-
"""Unit tests for paths.py — 路径工具模块"""
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch
import paths


def test_get_base_dir_returns_path():
    """get_base_dir 返回 Path 对象"""
    result = paths.get_base_dir()
    assert isinstance(result, Path)


def test_get_base_dir_exists():
    """get_base_dir 返回的目录存在"""
    result = paths.get_base_dir()
    assert result.exists()


def test_get_base_dir_source_mode():
    """源码模式下返回脚本所在目录"""
    with patch.object(sys, 'frozen', False, create=True):
        result = paths.get_base_dir()
        assert isinstance(result, Path)
        assert result.exists()


def test_get_base_dir_macos_app_path_does_not_crash():
    """macOS .app frozen 分支应能正常计算基础目录。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        app_dir = root / "BOSS_ResumeFilter.app"
        macos_dir = app_dir / "Contents" / "MacOS"
        macos_dir.mkdir(parents=True)
        exe_path = macos_dir / "BOSS_ResumeFilter"
        exe_path.write_text("", encoding="utf-8")

        with patch.object(sys, 'frozen', True, create=True), \
                patch.object(sys, 'platform', 'darwin'), \
                patch.object(sys, 'executable', str(exe_path)):
            result = paths.get_base_dir()

        assert result.resolve() == root.resolve()


def test_ensure_config_files_noop_in_source():
    """源码模式下 ensure_config_files 不做任何操作（无 _MEIPASS）"""
    with patch.object(sys, 'frozen', False, create=True):
        # 不应该抛异常
        paths.ensure_config_files(Path("/tmp/test"))


def test_ensure_config_files_noop_without_meipass():
    """frozen 模式但无 _MEIPASS 时不做任何操作"""
    with patch.object(sys, 'frozen', True, create=True):
        if hasattr(sys, '_MEIPASS'):
            delattr(sys, '_MEIPASS')
        paths.ensure_config_files(Path("/tmp/test"))


def test_base_dir_constant():
    """BASE_DIR 常量是有效路径"""
    assert isinstance(paths.BASE_DIR, Path)
    assert paths.BASE_DIR.exists()


def test_selectors_path_constant():
    """SELECTORS_PATH 指向 selectors.json"""
    assert isinstance(paths.SELECTORS_PATH, Path)
    assert paths.SELECTORS_PATH.name == "selectors.json"


def test_config_path_constant():
    """CONFIG_PATH 指向 job_config.json"""
    assert isinstance(paths.CONFIG_PATH, Path)
    assert paths.CONFIG_PATH.name == "job_config.json"


def test_api_config_path_reads_default_before_local_exists():
    """源码模式下无本机配置时读取发布默认模板。"""
    with tempfile.TemporaryDirectory() as tmpdir, \
            patch.object(sys, 'frozen', False, create=True):
        result = paths.get_api_config_path(Path(tmpdir))
        assert result.name == "api_config.json"


def test_api_config_path_writes_local_in_source_mode():
    """源码模式下用户配置写入 ignored 本机文件，避免污染发布模板。"""
    with tempfile.TemporaryDirectory() as tmpdir, \
            patch.object(sys, 'frozen', False, create=True):
        result = paths.get_api_config_path(Path(tmpdir), for_write=True)
        assert result.name == "api_config.local.json"


def test_api_config_path_uses_local_when_present():
    """源码模式下本机配置存在后，后续读取优先使用它。"""
    with tempfile.TemporaryDirectory() as tmpdir, \
            patch.object(sys, 'frozen', False, create=True):
        local_path = Path(tmpdir) / "api_config.local.json"
        local_path.write_text("{}", encoding="utf-8")
        result = paths.get_api_config_path(Path(tmpdir))
        assert result == local_path


def test_api_config_path_uses_default_in_frozen_mode():
    """打包后仍使用可写目录旁的 api_config.json。"""
    with tempfile.TemporaryDirectory() as tmpdir, \
            patch.object(sys, 'frozen', True, create=True):
        result = paths.get_api_config_path(Path(tmpdir), for_write=True)
        assert result.name == "api_config.json"


def test_candidates_path_constant():
    """CANDIDATES_PATH 指向 candidates_all.json"""
    assert isinstance(paths.CANDIDATES_PATH, Path)
    assert paths.CANDIDATES_PATH.name == "candidates_all.json"


def test_candidates_xlsx_path_constant():
    """CANDIDATES_XLSX_PATH 指向 candidates_all.xlsx"""
    assert isinstance(paths.CANDIDATES_XLSX_PATH, Path)
    assert paths.CANDIDATES_XLSX_PATH.name == "candidates_all.xlsx"
