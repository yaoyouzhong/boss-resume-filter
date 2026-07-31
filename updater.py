"""
自动更新模块
支持 Windows EXE 和 macOS 的自动更新
"""

import hashlib
import json
import logging
import os
import plistlib
import queue
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from ui_messagebox import messagebox
import ui_theme

import requests
import tkinter as tk

from constants import (
    UPDATE_TIMEOUT_CHANGELOG,
    UPDATE_TIMEOUT_DOWNLOAD,
    UPDATE_TIMEOUT_GITEE,
    UPDATE_TIMEOUT_GITHUB,
    UPDATE_TIMEOUT_GIT_PULL,
    UPDATE_TIMEOUT_RELEASE_NOTES_GITEE,
    UPDATE_TIMEOUT_RELEASE_NOTES_GITEE_RETRY,
    UPDATE_TIMEOUT_RELEASE_NOTES_GITHUB,
)
from paths import get_base_dir
from subprocess_utils import hidden_subprocess

subprocess = hidden_subprocess(subprocess)

logger = logging.getLogger(__name__)


_FONT_FAMILY = ui_theme.FONT_FAMILY


def _place_dialog_centered(dialog, parent, width, height):
    """将更新弹窗相对父窗口居中，并限制在屏幕可见范围内。"""
    if hasattr(dialog, "winfo_id"):
        try:
            from gui_main import _place_window_centered

            _place_window_centered(dialog, width, height, parent=parent)
            return
        except ImportError:
            pass

    parent.update_idletasks()
    dialog.update_idletasks()

    screen_width = dialog.winfo_screenwidth()
    screen_height = dialog.winfo_screenheight()
    if width > screen_width:
        width = max(1, int(screen_width * 0.9))
    if height > screen_height:
        height = max(1, int(screen_height * 0.85))

    parent_x = parent.winfo_rootx()
    parent_y = parent.winfo_rooty()
    parent_width = parent.winfo_width()
    parent_height = parent.winfo_height()

    x = parent_x + (parent_width - width) // 2
    y = parent_y + (parent_height - height) // 2
    y -= _get_parent_titlebar_center_offset(parent)
    x = min(max(0, x), max(0, screen_width - width))
    y = min(max(0, y), max(0, screen_height - height))
    dialog.geometry(f"{width}x{height}{x:+d}{y:+d}")
    _bind_parent_center_correction(dialog, parent, width, height, 0, 0, screen_width, screen_height)


def _bind_parent_center_correction(dialog, parent, width, height, screen_left, screen_top, screen_width, screen_height):
    """更新弹窗显示后用 Tk 实际坐标再校正一次父子中心。"""
    try:
        if getattr(dialog, "_parent_center_correction_bound", False):
            return
        dialog._parent_center_correction_bound = True

        def correct_once(event=None):
            try:
                dialog.unbind("<Map>", getattr(dialog, "_parent_center_correction_bind_id", ""))
            except tk.TclError:
                pass
            try:
                parent.update_idletasks()
                dialog.update_idletasks()
                parent_center_x = parent.winfo_rootx() + parent.winfo_width() // 2
                parent_center_y = parent.winfo_rooty() + parent.winfo_height() // 2
                dialog_center_x = dialog.winfo_rootx() + dialog.winfo_width() // 2
                dialog_center_y = dialog.winfo_rooty() + dialog.winfo_height() // 2
                dx = parent_center_x - dialog_center_x
                dy = parent_center_y - dialog_center_y
                if abs(dx) < 1 and abs(dy) < 1:
                    return
                new_x = dialog.winfo_rootx() + dx
                new_y = dialog.winfo_rooty() + dy
                max_x = screen_left + max(0, screen_width - width)
                max_y = screen_top + max(0, screen_height - height)
                new_x = min(max(screen_left, new_x), max_x)
                new_y = min(max(screen_top, new_y), max_y)
                dialog.geometry(f"{width}x{height}+{int(new_x)}+{int(new_y)}")
            except (tk.TclError, AttributeError):
                return

        bind_id = dialog.bind("<Map>", correct_once, add="+")
        dialog._parent_center_correction_bind_id = bind_id
        dialog.after(50, correct_once)
    except (tk.TclError, AttributeError):
        return


def _get_parent_titlebar_center_offset(parent):
    """估算父窗口标题栏导致的视觉中心下偏，只修正纵向中心。"""
    try:
        titlebar_height = int(parent.winfo_rooty()) - int(parent.winfo_y())
    except (tk.TclError, AttributeError, TypeError, ValueError):
        return 0
    if titlebar_height <= 0 or titlebar_height > 120:
        return 0
    return titlebar_height // 2


import logging

logger = logging.getLogger(__name__)


def get_current_version() -> str:
    """获取当前版本号"""
    try:
        # gui_main 是程序入口，updater 被调用时已在 sys.modules 中
        # 直接读取模块属性，无需解析源文件，兼容所有打包模式
        import gui_main
        return gui_main.__version__
    except Exception:
        logger.warning("获取当前版本失败，返回默认值", exc_info=True)
        return "0.0.0"


def _parse_version(v: str) -> tuple:
    """Parse a version string into a comparable tuple.

    Supports 'v' prefix and pads to 3 components for correct comparison.
    e.g., '2.11' -> (2, 11, 0), '2.11.1' -> (2, 11, 1)
    """
    try:
        parts = [int(x) for x in v.lstrip('vV').split('.')]
        while len(parts) < 3:
            parts.append(0)
        return tuple(parts)
    except Exception:
        return (0, 0, 0)


def _github_asset_integrity(
    repo: str,
    version: str,
    platform_key: str,
    asset: dict,
) -> dict[str, object]:
    """Resolve mandatory size/SHA256 metadata for a GitHub release asset."""
    try:
        release_size = int(asset.get("size"))
    except (TypeError, ValueError) as exc:
        raise ValueError("GitHub Release 文件大小无效") from exc
    if release_size <= 0:
        raise ValueError("GitHub Release 文件大小无效")

    digest = str(asset.get("digest") or "").strip()
    digest_match = re.fullmatch(r"sha256:([0-9a-fA-F]{64})", digest)
    if digest_match:
        return {
            "size": release_size,
            "sha256": digest_match.group(1).lower(),
        }

    manifest_url = f"https://raw.githubusercontent.com/{repo}/master/latest.json"
    response = requests.get(
        manifest_url,
        headers={"Accept": "application/json"},
        timeout=UPDATE_TIMEOUT_GITHUB,
    )
    response.raise_for_status()
    manifest = response.json()
    if not isinstance(manifest, dict):
        raise ValueError("GitHub 完整性清单格式无效")
    if str(manifest.get("version") or "").lstrip("vV") != version:
        raise ValueError("GitHub 完整性清单版本与 Release 不一致")

    metadata = (manifest.get("assets") or {}).get(platform_key)
    if not isinstance(metadata, dict):
        raise ValueError("GitHub 完整性清单缺少当前平台")
    try:
        manifest_size = int(metadata.get("size"))
    except (TypeError, ValueError) as exc:
        raise ValueError("GitHub 完整性清单文件大小无效") from exc
    sha256 = str(metadata.get("sha256") or "").strip().lower()
    if manifest_size != release_size:
        raise ValueError("GitHub 完整性清单文件大小与 Release 不一致")
    if not re.fullmatch(r"[0-9a-f]{64}", sha256):
        raise ValueError("GitHub 完整性清单 SHA256 无效")

    download_url = str(asset.get("browser_download_url") or "")
    manifest_url_for_asset = str((manifest.get("downloads") or {}).get(platform_key) or "")
    if not download_url or manifest_url_for_asset != download_url:
        raise ValueError("GitHub 完整性清单下载地址与 Release 不一致")
    return {"size": release_size, "sha256": sha256}


def check_github_release(repo="yaoyouzhong/boss-resume-filter"):
    """
    检查 GitHub Release 最新版本

    Returns:
        dict: {
            'latest': str,  # 最新版本号
            'current': str,  # 当前版本号
            'has_update': bool,  # 是否有更新
            'update_type': 'version' | None,  # 更新类型（GitHub 不支持 hash 比较）
            'release_info': dict,  # GitHub Release 信息
            'download_url': str,  # EXE 下载链接（Windows）
            'error': str  # 错误信息
        }
    """
    result = {
        'latest': None,
        'current': get_current_version(),
        'has_update': False,
        'update_type': None,
        'content_changed': False,
        'release_info': None,
        'download_url': None,
        'download_url_fallback': None,
        'asset_info': {},
        'error': None
    }

    try:
        # 调用 GitHub API
        api_url = f"https://api.github.com/repos/{repo}/releases/latest"
        headers = {'Accept': 'application/vnd.github.v3+json'}

        response = requests.get(api_url, headers=headers, timeout=UPDATE_TIMEOUT_GITHUB)
        response.raise_for_status()

        release = response.json()
        result['release_info'] = release

        # 提取版本号（tag_name 可能是 "v2.7" 或 "2.7"）
        tag = release.get('tag_name', '')
        latest_version = tag.lstrip('v')
        result['latest'] = latest_version

        # 比较版本号
        current_tuple = _parse_version(result['current'])
        latest_tuple = _parse_version(latest_version)

        if latest_tuple > current_tuple:
            result['has_update'] = True
            result['update_type'] = 'version'

        # 查找下载链接
        selected_asset = None
        platform_key = None
        if sys.platform == 'win32':
            # Windows: 查找 .exe
            for asset in release.get('assets', []):
                if asset.get('name', '').endswith('.exe'):
                    selected_asset = asset
                    platform_key = "windows"
                    result['download_url'] = asset.get('browser_download_url')
                    break
        elif sys.platform == 'darwin':
            # macOS: 查找 _mac.zip
            for asset in release.get('assets', []):
                if asset.get('name', '').endswith('_mac.zip'):
                    selected_asset = asset
                    platform_key = "macos"
                    result['download_url'] = asset.get('browser_download_url')
                    break

        if result['has_update']:
            if not selected_asset or not platform_key:
                raise ValueError("GitHub Release 缺少当前平台的更新文件")
            result['asset_info'] = _github_asset_integrity(
                repo,
                latest_version,
                platform_key,
                selected_asset,
            )

    except requests.exceptions.Timeout:
        result['error'] = "网络连接超时"
    except requests.exceptions.RequestException as e:
        result['error'] = f"网络请求失败: {e}"
    except Exception as e:
        result['error'] = f"检查更新失败: {e}"

    return result


def _get_gitee_latest_response(latest_json_url):
    """Fetch Gitee latest.json, retrying once for cold raw-file timeouts."""
    for attempt in range(2):
        try:
            return requests.get(latest_json_url, timeout=UPDATE_TIMEOUT_GITEE)
        except requests.exceptions.Timeout:
            if attempt == 0:
                continue
            raise


def check_gitee_latest(latest_json_url="https://gitee.com/yaoyouzhong/boss-resume-filter/raw/master/latest.json"):
    """
    从 Gitee 检查最新版本（国内备用源）

    Args:
        latest_json_url: Gitee 上 latest.json 的 URL

    Returns:
        dict: 与 check_github_release() 返回结构相同，额外包含：
            - update_type: 'version' | 'content' | None
            - content_changed: bool（版本号相同但文件内容不同）
    """
    result = {
        'latest': None,
        'current': get_current_version(),
        'has_update': False,
        'update_type': None,
        'content_changed': False,
        'release_info': None,
        'download_url': None,
        'download_url_fallback': None,
        'error': None
    }

    try:
        response = _get_gitee_latest_response(latest_json_url)
        response.raise_for_status()

        data = response.json()
        latest_version = data.get('version', '').lstrip('v')
        result['latest'] = latest_version

        # 比较版本号
        current_tuple = _parse_version(result['current'])
        latest_tuple = _parse_version(latest_version)
        version_is_newer = latest_tuple > current_tuple

        if version_is_newer:
            result['has_update'] = True
            result['update_type'] = 'version'
        elif latest_tuple == current_tuple:
            # 版本号相同，检查文件内容是否变化（重新打包场景）
            platform_key = 'windows' if sys.platform == 'win32' else 'macos'
            assets = data.get('assets', {})
            remote_sha256 = assets.get(platform_key, {}).get('sha256')

            if remote_sha256:
                local_sha256 = _get_current_exe_sha256()
                if local_sha256 and local_sha256.lower() != str(remote_sha256).lower():
                    result['has_update'] = True
                    result['update_type'] = 'content'
                    result['content_changed'] = True

        # 构造 release_info（兼容 GitHub 格式）
        result['release_info'] = {
            'tag_name': f"v{latest_version}",
            'body': data.get('release_notes', '无更新说明')
        }

        # 获取下载链接：优先使用 Gitee 国内下载链接，回退到 GitHub 链接
        downloads = data.get('downloads', {})
        downloads_cn = data.get('downloads_cn', {})
        assets = data.get('assets', {})
        if sys.platform == 'win32':
            result['download_url'] = downloads_cn.get('windows') or downloads.get('windows')
            result['download_url_fallback'] = downloads.get('windows')
            result['asset_info'] = assets.get('windows', {})
        elif sys.platform == 'darwin':
            result['download_url'] = downloads_cn.get('macos') or downloads.get('macos')
            result['download_url_fallback'] = downloads.get('macos')
            result['asset_info'] = assets.get('macos', {})

    except requests.exceptions.Timeout:
        result['error'] = "Gitee 连接超时"
    except requests.exceptions.RequestException as e:
        result['error'] = f"Gitee 请求失败: {e}"
    except Exception as e:
        result['error'] = f"检查更新失败: {e}"

    return result


def _file_sha256(path):
    """计算文件 SHA256，用于更新包完整性校验。"""
    digest = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


# 当前更新目标文件的 SHA256 缓存（session 内只计算一次）
_current_exe_sha256_cache: str | None = None


def _get_current_exe_sha256() -> str | None:
    """获取当前打包应用主程序的 SHA256，源码运行时返回 None。"""
    global _current_exe_sha256_cache
    if _current_exe_sha256_cache is not None:
        return _current_exe_sha256_cache

    # 源码运行时 sys.executable 是 python.exe / python，不是可更新产物。
    if not getattr(sys, 'frozen', False):
        return None

    exe_path = Path(sys.executable).resolve()
    if sys.platform == 'win32' and exe_path.suffix.lower() != '.exe':
        return None
    if sys.platform == 'darwin':
        current_app = exe_path
        while current_app.suffix != '.app' and current_app != current_app.parent:
            current_app = current_app.parent
        if current_app.suffix != '.app':
            return None

    try:
        _current_exe_sha256_cache = _file_sha256(exe_path)
        return _current_exe_sha256_cache
    except Exception:
        return None


def verify_downloaded_file(path, asset_info=None):
    """校验下载文件的大小和 SHA256。自动更新核心资产必须同时提供两项元数据。"""
    asset_info = asset_info or {}
    expected_size = asset_info.get('size')
    expected_sha256 = asset_info.get('sha256')

    if expected_size is None or not expected_sha256:
        return False, "更新源缺少文件大小或 SHA256 校验信息，已拒绝安装"

    if expected_size is not None:
        try:
            expected_size = int(expected_size)
        except (TypeError, ValueError):
            return False, f"更新源文件大小元数据无效: {expected_size}"
        actual_size = Path(path).stat().st_size
        if actual_size != expected_size:
            return False, f"文件大小不匹配: 期望 {expected_size} bytes，实际 {actual_size} bytes"

    magic_error = _validate_file_magic(path)
    if magic_error:
        return False, magic_error

    if expected_sha256:
        actual_sha256 = _file_sha256(path)
        if actual_sha256.lower() != str(expected_sha256).lower():
            return False, (
                "SHA256 不匹配: "
                f"期望 {expected_sha256}，实际 {actual_sha256}"
            )

    return True, None


def _validate_file_magic(path):
    """检查常见更新包文件头，防止 HTML 错误页等非目标文件进入安装流程。"""
    path = Path(path)
    suffix = path.suffix.lower()
    expected = None
    label = None
    if suffix == ".exe":
        expected = b"MZ"
        label = "EXE"
    elif suffix == ".zip":
        expected = b"PK"
        label = "ZIP"

    if not expected:
        return None

    try:
        with open(path, "rb") as f:
            actual = f.read(len(expected))
    except OSError as e:
        return f"无法读取更新包文件头: {e}"

    if actual != expected:
        return f"{label} 文件头无效，下载内容可能不是正确的更新包"
    return None


def download_file(url, dest_path, progress_callback=None):
    """
    下载文件，支持进度回调

    Args:
        url: 下载链接
        dest_path: 保存路径
        progress_callback: 进度回调函数 callback(downloaded, total)
    """
    try:
        response = requests.get(url, stream=True, timeout=UPDATE_TIMEOUT_DOWNLOAD)
        response.raise_for_status()

        total_size = int(response.headers.get('content-length', 0))
        downloaded = 0

        with open(dest_path, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192):
                if chunk:
                    f.write(chunk)
                    downloaded += len(chunk)
                    if progress_callback:
                        progress_callback(downloaded, total_size)

        return True, None
    except Exception as e:
        # 清理残缺文件，防止调用方拿到不完整的下载
        try:
            Path(dest_path).unlink(missing_ok=True)
        except OSError:
            pass
        return False, str(e)


def download_and_verify_file(url, dest_path, asset_info=None, progress_callback=None):
    """下载文件，并在有元数据时校验大小和 SHA256。"""
    success, error = download_file(url, dest_path, progress_callback)
    if not success:
        return False, error

    verified, verify_error = verify_downloaded_file(dest_path, asset_info)
    if not verified:
        try:
            Path(dest_path).unlink(missing_ok=True)
        except OSError:
            pass
        return False, verify_error

    return True, None


def mark_update_success_and_cleanup():
    """新版本成功进入 GUI 后写入启动标记。

    Windows 下保留 .old，便于用户在新版本异常时快速恢复上一版。
    """
    if not getattr(sys, 'frozen', False):
        return

    try:
        marker = os.environ.get("BOSS_UPDATE_MARKER")
        if marker:
            Path(marker).write_text(str(time.time()), encoding="utf-8")
            print(f"[更新] 已写入启动成功标记: {marker}")

    except OSError as e:
        print(f"[更新] 写入启动成功标记失败: {e}")


def notify_previous_update_failure(root):
    """启动后提示上次自动更新脚本留下的失败信息。"""
    if not getattr(sys, 'frozen', False):
        return

    failed_file = Path(sys.executable + ".update_failed.txt")
    if not failed_file.exists():
        return

    try:
        detail = failed_file.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        detail = ""

    try:
        messagebox.show_failure(
            "上次更新未完成",
            headline="上次自动更新没有完成",
            message="程序已保留或回滚到可用版本。",
            detail=detail or None,
            notice="如需继续更新，请点“检查更新”重试。",
            parent=root,
        )
    except tk.TclError:
        pass


def _windows_update_cache_root(base_dir=None):
    """返回主程序目录下的 Windows 更新缓存根目录。"""
    return Path(base_dir or get_base_dir()).resolve() / "updates"


def _windows_update_cache_dir(version, base_dir=None):
    """按目标版本隔离更新缓存，避免不同版本安装包互相覆盖。"""
    return (
        _windows_update_cache_root(base_dir)
        / _normalize_update_version(version)
    )


def _windows_update_cache_path(version, base_dir=None):
    """返回目标版本已完成并通过校验的 Windows 安装包路径。"""
    return (
        _windows_update_cache_dir(version, base_dir)
        / "BOSS_ResumeFilter_new.exe"
    )


def _get_cached_windows_update(result, base_dir=None):
    """返回与当前远端元数据完全一致的已下载 Windows 安装包。"""
    latest = result.get("latest")
    if not latest:
        return None
    try:
        cached_path = _windows_update_cache_path(latest, base_dir)
    except ValueError:
        return None
    if not cached_path.is_file():
        return None

    verified, _error = verify_downloaded_file(
        cached_path,
        result.get("asset_info") or {},
    )
    return cached_path if verified else None


def _is_managed_windows_update_dir(path, app_dir=None):
    """只允许更新助手清理自己创建的临时目录或版本缓存目录。"""
    path = Path(path).resolve()
    system_temp = Path(tempfile.gettempdir()).resolve()
    if path.parent == system_temp and path.name.startswith("boss_update_download_"):
        return True

    cache_root = _windows_update_cache_root(app_dir)
    return (
        path.parent == cache_root
        and re.fullmatch(r"\d+\.\d+(?:\.\d+)?", path.name) is not None
    )


def _clean_pyinstaller_environment(marker_path=None):
    """为独立更新程序或更新后的主程序构造干净的启动环境。"""
    env = dict(os.environ)
    env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    for name in list(env):
        if name.startswith("_PYI_"):
            env.pop(name, None)
    if marker_path:
        env["BOSS_UPDATE_MARKER"] = str(marker_path)
    else:
        env.pop("BOSS_UPDATE_MARKER", None)
    return env


def _wait_for_windows_process_exit(pid, timeout_seconds):
    """等待指定 Windows 进程退出，不创建 tasklist 控制台子进程。"""
    if not pid:
        return True

    import ctypes

    synchronize = 0x00100000
    handle = ctypes.windll.kernel32.OpenProcess(synchronize, False, int(pid))
    if not handle:
        return True
    try:
        result = ctypes.windll.kernel32.WaitForSingleObject(
            handle,
            max(0, int(float(timeout_seconds) * 1000)),
        )
        return result == 0
    finally:
        ctypes.windll.kernel32.CloseHandle(handle)


def _wait_for_file(path, timeout_seconds):
    """等待启动确认文件出现。"""
    deadline = time.monotonic() + float(timeout_seconds)
    while time.monotonic() < deadline:
        if Path(path).exists():
            return True
        time.sleep(0.25)
    return Path(path).exists()


def _terminate_process(process):
    """尽力结束更新后未能正常启动的进程。"""
    if process is None or process.poll() is not None:
        return
    try:
        process.terminate()
        process.wait(timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        try:
            process.kill()
        except OSError:
            pass


def _launch_updated_windows_app(exe_path, marker_path):
    """用干净的 PyInstaller 环境启动更新后的应用。"""
    return subprocess.Popen(
        [str(exe_path)],
        cwd=str(Path(exe_path).parent),
        env=_clean_pyinstaller_environment(marker_path),
        close_fds=True,
    )


def _schedule_update_temp_cleanup(temp_dir, app_dir=None):
    """更新助手退出后清理本轮临时目录。"""
    temp_dir = Path(temp_dir).resolve()
    if not _is_managed_windows_update_dir(temp_dir, app_dir):
        return

    system_temp = Path(tempfile.gettempdir()).resolve()
    cleanup_script = system_temp / f"boss_update_cleanup_{os.getpid()}.bat"
    cleanup_script.write_text(
        "@echo off\n"
        "timeout /t 3 /nobreak >nul\n"
        f'rmdir /s /q "{temp_dir}"\n'
        'del /f /q "%~f0"\n',
        encoding="utf-8",
    )
    subprocess.Popen(
        ["cmd", "/c", str(cleanup_script)],
        cwd=str(system_temp),
        close_fds=True,
    )


def _normalize_update_version(version):
    """规范备份文件使用的版本号，拒绝把任意文本拼入文件名。"""
    normalized = str(version or "").strip().lstrip("vV")
    if not re.fullmatch(r"\d+\.\d+(?:\.\d+)?", normalized):
        raise ValueError(f"无法识别升级前版本号：{version}")
    return normalized


def _versioned_backup_path(exe_path, version):
    """返回形如 BOSS_ResumeFilter.exe.2.25.1 的版本备份路径。"""
    exe_path = Path(exe_path)
    return Path(f"{exe_path}.{_normalize_update_version(version)}")


def _other_versioned_backups(exe_path, keep_path):
    """列出同一程序更早的版本号备份，不匹配更新标记或失败记录。"""
    exe_path = Path(exe_path)
    keep_path = Path(keep_path)
    pattern = re.compile(
        rf"^{re.escape(exe_path.name)}\.\d+\.\d+(?:\.\d+)?$"
    )
    return [
        candidate
        for candidate in exe_path.parent.iterdir()
        if candidate != keep_path and pattern.fullmatch(candidate.name)
    ]


def _apply_windows_update(payload, on_progress):
    """在独立更新助手中替换程序，并以启动标记确认更新结果。"""
    old_exe = Path(payload["old_exe"]).resolve()
    new_exe = Path(payload["new_exe"]).resolve()
    marker_path = Path(payload["marker_path"]).resolve()
    failed_file = Path(str(old_exe) + ".update_failed.txt")
    backup_exe = _versioned_backup_path(old_exe, payload.get("old_version"))
    previous_backup = Path(str(backup_exe) + ".previous")
    legacy_backup = Path(str(old_exe) + ".old")
    legacy_previous_backup = Path(str(legacy_backup) + ".previous")
    log_path = Path(tempfile.gettempdir()) / "boss_resume_filter_update.log"
    launched_process = None
    backup_created = False
    previous_backup_rotated = False
    old_process_exited = False

    def log(message):
        timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
        with open(log_path, "a", encoding="utf-8") as file_obj:
            file_obj.write(f"[{timestamp}] {message}\n")

    try:
        log(f"Starting visible update; source={payload.get('source', 'manual')}")
        on_progress(8, "正在准备安装", "正在等待原程序安全退出…")
        if not _wait_for_windows_process_exit(payload.get("old_pid"), 60):
            raise RuntimeError("原程序未能在 60 秒内退出")
        old_process_exited = True

        on_progress(20, "正在校验更新包", "确认下载文件完整且可用…")
        verified, verify_error = verify_downloaded_file(
            new_exe,
            payload.get("asset_info") or {},
        )
        if not verified:
            raise RuntimeError(verify_error or "更新包校验失败")

        on_progress(35, "正在备份当前版本", "保留旧版本，以便安装失败时自动恢复…")
        if previous_backup.exists():
            previous_backup.unlink()
        if backup_exe.exists():
            shutil.move(str(backup_exe), str(previous_backup))
            previous_backup_rotated = True
        shutil.move(str(old_exe), str(backup_exe))
        backup_created = True

        on_progress(55, "正在安装新版本", "正在替换应用程序文件…")
        shutil.copy2(new_exe, old_exe)
        if old_exe.stat().st_size != new_exe.stat().st_size:
            raise RuntimeError("安装后的文件大小与下载文件不一致")
        if _file_sha256(old_exe) != _file_sha256(new_exe):
            raise RuntimeError("安装后的文件校验值与下载文件不一致")

        on_progress(75, "正在启动新版本", "应用即将自动重新打开…")
        marker_path.unlink(missing_ok=True)
        launched_process = _launch_updated_windows_app(old_exe, marker_path)

        on_progress(88, "正在确认启动结果", "请稍候，正在确认新版本可以正常启动…")
        if not _wait_for_file(marker_path, 45):
            log("First startup marker not found; retrying once")
            _terminate_process(launched_process)
            time.sleep(3)
            launched_process = _launch_updated_windows_app(old_exe, marker_path)
            if not _wait_for_file(marker_path, 90):
                raise RuntimeError("新版本两次启动均未返回成功确认")

        try:
            stale_backups = _other_versioned_backups(old_exe, backup_exe)
        except OSError as cleanup_exc:
            stale_backups = []
            log(f"Backup scan warning: {cleanup_exc}")
        cleanup_paths = [
            marker_path,
            failed_file,
            previous_backup,
            *stale_backups,
            legacy_backup,
            legacy_previous_backup,
        ]
        for cleanup_path in cleanup_paths:
            try:
                cleanup_path.unlink(missing_ok=True)
            except OSError as cleanup_exc:
                log(f"Cleanup warning for {cleanup_path}: {cleanup_exc}")
        log(f"Update completed; previous version kept at {backup_exe}")
        on_progress(100, "更新安装完成", "新版本已成功启动，本窗口即将自动关闭。")
        return True, None
    except Exception as exc:
        log(f"Update failed: {exc}")
        _terminate_process(launched_process)
        rollback_error = None
        if backup_created:
            try:
                old_exe.unlink(missing_ok=True)
                shutil.move(str(backup_exe), str(old_exe))
                log("Rolled back to previous executable")
                if previous_backup_rotated and previous_backup.exists():
                    shutil.move(str(previous_backup), str(backup_exe))
            except (OSError, shutil.Error) as rollback_exc:
                rollback_error = str(rollback_exc)
                log(f"Rollback failed: {rollback_error}")
        elif previous_backup_rotated and previous_backup.exists():
            try:
                shutil.move(str(previous_backup), str(backup_exe))
            except (OSError, shutil.Error) as restore_exc:
                rollback_error = f"原备份恢复失败：{restore_exc}"
                log(rollback_error)
        if old_process_exited and not rollback_error and old_exe.exists():
            try:
                subprocess.Popen(
                    [str(old_exe)],
                    cwd=str(old_exe.parent),
                    env=_clean_pyinstaller_environment(),
                    close_fds=True,
                )
            except OSError as relaunch_exc:
                rollback_error = f"原版本重新打开失败：{relaunch_exc}"
                log(rollback_error)

        detail = f"自动更新失败：{exc}\n详细日志：{log_path}"
        if rollback_error:
            detail += f"\n自动恢复失败：{rollback_error}"
        elif old_process_exited:
            detail += "\n已恢复并重新打开原版本。"
        else:
            detail += "\n原程序仍在运行，未替换任何应用文件。"
        try:
            failed_file.write_text(detail, encoding="utf-8")
        except OSError:
            pass
        return False, detail


def run_windows_update_helper(payload_path):
    """显示独立安装进度窗口；主程序退出后该窗口继续完成更新。"""
    from tkinter import ttk

    payload_path = Path(payload_path).resolve()
    payload = json.loads(payload_path.read_text(encoding="utf-8"))
    payload["old_version"] = _normalize_update_version(payload.get("old_version"))
    temp_dir = Path(payload["temp_dir"]).resolve()
    new_exe = Path(payload["new_exe"]).resolve()
    ready_path = Path(payload["ready_path"]).resolve()
    old_exe = Path(payload["old_exe"]).resolve()
    marker_path = Path(payload["marker_path"]).resolve()
    if (
        payload_path.parent != temp_dir
        or not _is_managed_windows_update_dir(temp_dir, old_exe.parent)
        or new_exe.parent != temp_dir
        or ready_path.parent != temp_dir
        or old_exe.suffix.lower() != ".exe"
        or new_exe.suffix.lower() != ".exe"
        or marker_path != Path(str(old_exe) + ".update_ok")
    ):
        raise ValueError("更新助手参数不合法")

    events = queue.Queue()
    root = tk.Tk()
    root.title("正在安装更新")
    root.configure(bg="#FFFFFF")
    root.resizable(False, False)
    try:
        from gui_main import _set_search_window_icon

        _set_search_window_icon(root)
    except Exception:
        pass
    width, height = 620, 270
    x = max(0, (root.winfo_screenwidth() - width) // 2)
    y = max(0, (root.winfo_screenheight() - height) // 2)
    root.geometry(f"{width}x{height}+{x}+{y}")

    tk.Label(
        root,
        text="正在安装更新",
        font=(_FONT_FAMILY, 22, "bold"),
        bg="#FFFFFF",
        fg="#172033",
    ).pack(pady=(34, 10))
    detail_var = tk.StringVar(value="更新即将完成，应用将自动重启，请稍候…")
    tk.Label(
        root,
        textvariable=detail_var,
        font=(_FONT_FAMILY, 11),
        bg="#FFFFFF",
        fg="#667085",
    ).pack()
    progress_var = tk.DoubleVar(value=3)
    ttk.Progressbar(
        root,
        mode="determinate",
        maximum=100,
        variable=progress_var,
        length=540,
    ).pack(pady=(34, 12))
    stage_var = tk.StringVar(value="正在启动安装程序…")
    stage_label = tk.Label(
        root,
        textvariable=stage_var,
        font=(_FONT_FAMILY, 10),
        bg="#FFFFFF",
        fg="#98A2B3",
    )
    stage_label.pack()
    close_button = ttk.Button(root, text="关闭", command=root.destroy, state="disabled")
    root.protocol("WM_DELETE_WINDOW", lambda: None)
    update_result = {"success": False}

    def on_progress(percent, stage, detail):
        events.put(("progress", percent, stage, detail))

    def worker():
        success, error = _apply_windows_update(payload, on_progress)
        events.put(("finished", success, error))

    def poll_events():
        try:
            while True:
                event = events.get_nowait()
                if event[0] == "progress":
                    _, percent, stage, detail = event
                    progress_var.set(percent)
                    stage_var.set(stage)
                    detail_var.set(detail)
                else:
                    _, success, _error = event
                    if success:
                        update_result["success"] = True
                        root.after(1800, root.destroy)
                    else:
                        progress_var.set(0)
                        stage_var.set("更新未完成")
                        stage_label.configure(fg=ui_theme.DANGER_TEXT)
                        detail_var.set(
                            "安装没有成功，已尝试恢复并重新打开原版本。\n"
                            "可关闭此窗口，并根据应用内提示重试。"
                        )
                        close_button.pack(pady=(14, 0))
                        close_button.configure(state="normal")
                        root.protocol("WM_DELETE_WINDOW", root.destroy)
        except queue.Empty:
            pass
        if root.winfo_exists():
            root.after(80, poll_events)

    def mark_ready():
        ready_path.write_text("ready", encoding="utf-8")
        threading.Thread(target=worker, daemon=True).start()

    root.after(80, mark_ready)
    root.after(80, poll_events)
    root.mainloop()
    if update_result["success"]:
        _schedule_update_temp_cleanup(temp_dir, old_exe.parent)


def update_windows(
    new_exe_path,
    current_exe_path,
    source="manual",
    asset_info=None,
    old_version=None,
):
    """启动独立可见的 Windows 更新助手，并等待其窗口就绪。"""
    try:
        new_exe = Path(new_exe_path).resolve()
        current_exe = Path(current_exe_path).resolve()
        old_version = _normalize_update_version(old_version)
        temp_dir = new_exe.parent
        helper_exe = temp_dir / "BOSS_ResumeFilter_updater.exe"
        payload_path = temp_dir / "update_payload.json"
        ready_path = temp_dir / "update_helper.ready"
        marker_path = Path(str(current_exe) + ".update_ok")

        shutil.copy2(current_exe, helper_exe)
        payload = {
            "old_exe": str(current_exe),
            "new_exe": str(new_exe),
            "temp_dir": str(temp_dir),
            "marker_path": str(marker_path),
            "ready_path": str(ready_path),
            "old_pid": os.getpid(),
            "source": str(source or "manual"),
            "asset_info": dict(asset_info or {}),
            "old_version": old_version,
        }
        payload_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        ready_path.unlink(missing_ok=True)

        process = subprocess.Popen(
            [str(helper_exe), "--apply-windows-update", str(payload_path)],
            cwd=str(temp_dir),
            env=_clean_pyinstaller_environment(),
            close_fds=True,
        )
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if ready_path.exists():
                return True, None
            if process.poll() is not None:
                return False, "独立安装窗口未能启动"
            time.sleep(0.1)

        _terminate_process(process)
        return False, "独立安装窗口启动超时"
    except Exception as exc:
        return False, str(exc)


def update_macos():
    """
    macOS 更新逻辑

    执行 git pull，然后提示用户重启应用
    """
    try:
        base_dir = get_base_dir()

        # 检查是否在 git 仓库中
        git_dir = base_dir / ".git"
        if not git_dir.exists():
            return False, "当前不是 git 仓库，无法自动更新"

        # 执行 git pull
        result = subprocess.run(
            ['git', 'pull', 'origin', 'master'],
            cwd=base_dir,
            capture_output=True,
            text=True,
            timeout=UPDATE_TIMEOUT_GIT_PULL
        )

        if result.returncode != 0:
            return False, f"git pull 失败: {result.stderr}"

        # 检查是否有更新
        if "Already up to date" in result.stdout:
            return True, "已经是最新版本"

        return True, "更新成功，请重启应用"

    except subprocess.TimeoutExpired:
        return False, "git pull 超时"
    except Exception as e:
        return False, str(e)


def update_macos_app(zip_path, current_app_path):
    """
    macOS .app 更新逻辑

    解压 ZIP 包，替换旧的 .app bundle，然后重启应用

    Args:
        zip_path: 下载的 ZIP 文件路径
        current_app_path: 当前 .app bundle 路径
    """
    try:
        # 用 ditto 解压，保留 .app bundle 内的 symlink、权限和扩展属性。
        # zipfile.extractall() 会破坏 Python.framework，导致更新后的 app 无法打开。
        temp_dir = Path(tempfile.mkdtemp())
        subprocess.run(
            ["ditto", "-x", "-k", str(zip_path), str(temp_dir)],
            check=True,
            capture_output=True,
            text=True,
        )

        # 找到解压后的 .app
        app_candidates = list(temp_dir.glob("*.app")) + list(temp_dir.glob("*/*.app"))
        new_app_path = app_candidates[0] if app_candidates else None

        if not new_app_path:
            return False, "ZIP 包中未找到 .app"

        info_plist = new_app_path / "Contents" / "Info.plist"
        if not info_plist.exists():
            return False, "ZIP 包中的 .app 缺少 Info.plist"

        with open(info_plist, "rb") as f:
            bundle_info = plistlib.load(f)
        executable_name = bundle_info.get("CFBundleExecutable")
        if not executable_name:
            return False, "ZIP 包中的 .app 缺少 CFBundleExecutable"

        executable_path = new_app_path / "Contents" / "MacOS" / executable_name
        if not executable_path.exists():
            return False, f"ZIP 包中的主程序不存在: {executable_name}"
        executable_path.chmod(executable_path.stat().st_mode | 0o755)

        # 生成替换脚本
        # 脚本写入 /tmp/（稳定位置），不放在 temp_dir 内，
        # 避免 sys.exit(0) 退出时 temp_dir 被 OS 清理导致脚本丢失
        # ditto 保留所有资源分支和扩展属性（cp -R 可能丢失）
        # xattr -cr 清除隔离属性，防止 Gatekeeper 拦截
        # 日志写入 /tmp/boss_update.log 便于诊断
        current_pid = os.getpid()
        quoted_current_app = shlex.quote(str(current_app_path))
        quoted_new_app = shlex.quote(str(new_app_path))
        quoted_temp_dir = shlex.quote(str(temp_dir))
        marker_path = Path(tempfile.gettempdir()) / "boss_update_ok"
        quoted_marker = shlex.quote(str(marker_path))

        script = f'''#!/bin/bash
set -e
exec > /tmp/boss_update.log 2>&1
OLD_APP={quoted_current_app}
NEW_APP={quoted_new_app}
TEMP_DIR={quoted_temp_dir}
MARKER_FILE={quoted_marker}
BACKUP_APP="${{OLD_APP}}.backup"
FAILED_FILE="${{OLD_APP}}.update_failed.txt"
OLD_PID={current_pid}

rollback() {{
    echo "[$(date)] Rolling back app update"
    rm -rf "$OLD_APP"
    if [ -d "$BACKUP_APP" ]; then
        mv "$BACKUP_APP" "$OLD_APP"
    fi
}}

echo "[$(date)] Starting update"
echo "[$(date)] Waiting for old process $OLD_PID to exit"
for i in {{1..60}}; do
    if ! kill -0 "$OLD_PID" 2>/dev/null; then
        break
    fi
    sleep 0.5
done

if kill -0 "$OLD_PID" 2>/dev/null; then
    echo "[$(date)] Old process did not exit in time"
    exit 1
fi

if [ ! -d "$NEW_APP" ]; then
    echo "[$(date)] New app not found: $NEW_APP"
    exit 1
fi

echo "[$(date)] Removing old app"
rm -rf "$BACKUP_APP"
if [ -d "$OLD_APP" ]; then
    mv "$OLD_APP" "$BACKUP_APP"
fi
echo "[$(date)] Copying new app with ditto"
if ! ditto "$NEW_APP" "$OLD_APP"; then
    rollback
    exit 1
fi
echo "[$(date)] Restoring executable permission"
EXECUTABLE=$(/usr/libexec/PlistBuddy -c 'Print :CFBundleExecutable' "$OLD_APP/Contents/Info.plist")
if [ -z "$EXECUTABLE" ] || [ ! -f "$OLD_APP/Contents/MacOS/$EXECUTABLE" ]; then
    echo "[$(date)] New app executable missing"
    rollback
    exit 1
fi
chmod +x "$OLD_APP/Contents/MacOS/$EXECUTABLE" || {{
    rollback
    exit 1
}}
echo "[$(date)] Clearing quarantine attributes"
xattr -cr "$OLD_APP" 2>/dev/null || true
echo "[$(date)] Opening app"
rm -f "$MARKER_FILE"
BOSS_UPDATE_MARKER="$MARKER_FILE" "$OLD_APP/Contents/MacOS/$EXECUTABLE" &
NEW_PID=$!
echo "[$(date)] Started new process PID=$NEW_PID"
echo "[$(date)] Waiting for startup marker $MARKER_FILE"
for i in {{1..90}}; do
    if [ -f "$MARKER_FILE" ]; then
        break
    fi
    sleep 1
done

if [ ! -f "$MARKER_FILE" ]; then
    echo "[$(date)] Startup marker not found"
    cat > "$FAILED_FILE" <<EOF
自动更新失败，已回滚到旧版本。
失败时间: $(date)
详细日志: /tmp/boss_update.log
EOF
    kill "$NEW_PID" 2>/dev/null || true
    rollback
    exit 1
fi

echo "[$(date)] Cleanup"
rm -f "$MARKER_FILE"
rm -rf "$BACKUP_APP"
rm -rf "$TEMP_DIR"
rm -f "$0"
'''

        # 写入 /tmp/（不在 temp_dir 内，不会随进程退出被清理）
        script_path = Path(tempfile.gettempdir()) / "boss_update.sh"
        with open(script_path, 'w', encoding='utf-8', newline='\n') as f:
            f.write(script)
        script_path.chmod(0o755)

        # 启动脚本：start_new_session=True 脱离父进程组，
        # 确保 sys.exit(0) 退出时脚本不会被 macOS 连带杀掉
        subprocess.Popen(
            ['bash', str(script_path)],
            close_fds=True,
            start_new_session=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        return True, "更新成功，程序即将重启"

    except Exception as e:
        return False, str(e)


def exit_for_update(root):
    """退出当前 GUI 进程，让外部更新脚本替换并重启应用。"""
    try:
        root.destroy()
    except tk.TclError:
        pass
    os._exit(0)


def check_and_update_gui(root: tk.Tk, silent: bool = False, on_complete=None, gui=None,
                         source: str = "manual", on_defer=None) -> None:
    """
    GUI 版本的更新检查和执行

    Args:
        root: tkinter 根窗口
        silent: 是否静默检查（不显示"已是最新版本"提示）
        gui: BossFilterGUI 实例（用于字体缩放和配色）
        source: 更新触发来源，用于日志区分 startup/manual
        on_defer: 用户选择稍后提醒时的回调
    """
    def do_check():
        # 优先尝试 Gitee（国内快）
        result = check_gitee_latest()

        if result['error']:
            # Gitee 请求失败，回退到 GitHub
            if not silent:
                print(f"[更新] Gitee 检查失败: {result['error']}，尝试 GitHub...")
            result = check_github_release()
        elif not result['has_update']:
            # Gitee 返回成功但无更新，用 GitHub 复核（防止 Gitee 镜像同步延迟）
            gh = check_github_release()
            if not gh['error'] and gh['has_update']:
                print(f"[更新] GitHub 发现新版本 v{gh['latest']}，使用 GitHub 结果")
                result = gh

        # 后台获取远端 CHANGELOG 段落（避免主线程阻塞）
        if result.get('has_update') and result.get('latest'):
            changelog_body = _fetch_changelog_section(result['latest'])
            if changelog_body:
                result['changelog_body'] = changelog_body
            if sys.platform == "win32":
                cached_update = _get_cached_windows_update(result)
                if cached_update:
                    result["cached_update_path"] = str(cached_update)

        # 回到主线程处理结果
        root.after(0, lambda: handle_result(result))

    def handle_result(result):
        if result['error']:
            if not silent:
                messagebox.show_failure(
                    "检查更新",
                    headline="暂时无法检查更新",
                    message="没有获取到可用的版本信息。",
                    detail=result['error'],
                    notice="请检查网络连接后重试。",
                    parent=root,
                )
            if on_complete:
                on_complete(result)
            return

        if not result['has_update']:
            if not silent:
                messagebox.showinfo(
                    "检查更新",
                    f"当前已是最新版本 v{result['current']}",
                    parent=root,
                    min_width=500,
                    font_delta=-1,
                    compact_action=True,
                )
            if on_complete:
                on_complete(result)
            return

        # 有新版本，显示更新对话框
        show_update_dialog(root, result, gui=gui, source=source, on_defer=on_defer)
        if on_complete:
            on_complete(result)

    # 启动后台检查
    threading.Thread(target=do_check, daemon=True).start()


def _fetch_changelog_section(target_version):
    """从远端 CHANGELOG.md 提取目标版本段落，与主界面版本历史/README/Release 同源。
    Gitee 优先（国内快），GitHub fallback。"""
    from changelog_parser import extract_changelog_section

    urls = [
        "https://gitee.com/yaoyouzhong/boss-resume-filter/raw/master/CHANGELOG.md",
        "https://raw.githubusercontent.com/yaoyouzhong/boss-resume-filter/master/CHANGELOG.md",
    ]
    content = None
    for url in urls:
        try:
            resp = requests.get(url, timeout=UPDATE_TIMEOUT_CHANGELOG)
            resp.raise_for_status()
            content = resp.text
            break
        except Exception:
            continue
    if not content:
        return None

    return extract_changelog_section(content, target_version)


_RELEASE_NOTES_CACHE_TTL = 60 * 60


def _release_notes_cache_path(base_dir=None):
    """Return the small cache file used by the GUI changelog dialog."""
    return Path(base_dir or get_base_dir()) / "release_notes_cache.json"


def get_cached_release_notes(version, *, max_age_seconds=_RELEASE_NOTES_CACHE_TTL, base_dir=None):
    """Return cached current-version Release Notes when fresh enough."""
    cache_path = _release_notes_cache_path(base_dir)
    try:
        data = json.loads(cache_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        return None

    target = str(version).lstrip("vV")
    if str(data.get("version", "")).lstrip("vV") != target:
        return None
    try:
        fetched_at = float(data.get("fetched_at", 0))
    except (TypeError, ValueError):
        return None
    if max_age_seconds is not None and time.time() - fetched_at > max_age_seconds:
        return None

    notes = data.get("release_notes")
    return notes.strip() if isinstance(notes, str) and notes.strip() else None


def _write_release_notes_cache(version, release_notes, *, source="", base_dir=None):
    """Best-effort cache write for remote Release Notes."""
    if not release_notes:
        return
    cache_path = _release_notes_cache_path(base_dir)
    payload = {
        "version": str(version).lstrip("vV"),
        "source": source,
        "fetched_at": time.time(),
        "release_notes": release_notes.strip(),
    }
    try:
        cache_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        return


def fetch_current_release_notes(version, *, use_cache=True, base_dir=None):
    """Fetch only the current version's remote Release Notes.

    The changelog dialog calls this from a background thread. Keep timeouts short
    so the remote correction path never competes with the local first paint.
    """
    target = str(version).lstrip("vV")
    if use_cache:
        cached = get_cached_release_notes(target, base_dir=base_dir)
        if cached:
            return cached

    # Gitee latest.json is the primary path for domestic users. Try once with a
    # short timeout, then retry with a longer timeout before falling back.
    gitee_url = "https://gitee.com/yaoyouzhong/boss-resume-filter/raw/master/latest.json"
    for timeout in (UPDATE_TIMEOUT_RELEASE_NOTES_GITEE, UPDATE_TIMEOUT_RELEASE_NOTES_GITEE_RETRY):
        try:
            resp = requests.get(gitee_url, timeout=timeout)
            resp.raise_for_status()
            data = resp.json()
            if str(data.get("version", "")).lstrip("vV") == target:
                notes = data.get("release_notes")
                if isinstance(notes, str) and notes.strip():
                    notes = notes.strip()
                    _write_release_notes_cache(target, notes, source="gitee", base_dir=base_dir)
                    return notes
            break
        except Exception:
            continue

    # Fallback to the exact GitHub Release tag, not the full release list.
    try:
        resp = requests.get(
            f"https://api.github.com/repos/yaoyouzhong/boss-resume-filter/releases/tags/v{target}",
            headers={'Accept': 'application/vnd.github.v3+json'},
            timeout=UPDATE_TIMEOUT_RELEASE_NOTES_GITHUB,
        )
        resp.raise_for_status()
        notes = resp.json().get("body")
        if isinstance(notes, str) and notes.strip():
            notes = notes.strip()
            _write_release_notes_cache(target, notes, source="github", base_dir=base_dir)
            return notes
    except Exception:
        pass

    return None


def show_update_dialog(root, result, gui=None, source="manual", on_defer=None):
    """显示更新对话框（使用 GUI 实例的字体缩放和配色方案）"""
    from tkinter import ttk
    from gui_dialogs import render_changelog_text

    # 缩放参数（有 gui 实例时用它，否则退化为 1.0）
    font_scale = getattr(gui, 'font_scale', 1.0)
    layout_scale = (getattr(gui, 'dpi_scale', 1.0)
                    * getattr(gui, 'zoom_factor', 1.0))
    font_family = getattr(gui, 'FONT_FAMILY', _FONT_FAMILY)
    font_family_bold = getattr(gui, 'FONT_FAMILY_SEMIBOLD', _FONT_FAMILY)
    colors = getattr(gui, 'colors', None) or ui_theme.build_palette()

    dialog = tk.Toplevel(root)
    dialog.title("发现新版本")
    dialog.transient(root)
    dialog.grab_set()
    dialog.resizable(True, True)
    dialog.configure(bg=colors['bg_card'])

    # 居中显示（按缩放调整尺寸）
    # Mac 上 font_scale 可能大于 layout_scale（font_boost 补偿），窗口高度需用 font_scale
    # 否则 Text 控件内容会溢出，导致底部按钮不可见
    height_scale = max(layout_scale, font_scale)
    dw = int(700 * layout_scale)
    dh = int(520 * height_scale)
    dialog.minsize(int(560 * layout_scale), int(420 * height_scale))
    _place_dialog_centered(dialog, root, dw, dh)

    pad = lambda v: int(v * layout_scale)
    fs = lambda size: int(size * font_scale)

    # 标题行：根据更新类型显示不同文字
    update_type = result.get('update_type')
    if update_type == 'content':
        title_text = f"v{result['current']} 内容已更新"
    else:
        title_text = f"v{result['current']} → v{result['latest']}"
    tk.Label(
        dialog,
        text=title_text,
        font=(font_family_bold, fs(13)),
        bg=colors['bg_card'], fg=colors['text_primary']
    ).pack(pady=(pad(15), pad(5)))

    # 更新内容：后台预取的远端 CHANGELOG 段落优先（### 格式，与主界面版本历史一致），fallback 用 latest.json release_notes
    target_version = result['latest']
    body = result.get('changelog_body') or result.get('release_info', {}).get('body', '无更新说明')

    content_frame = tk.LabelFrame(dialog, text="更新内容",
                                  padx=pad(10), pady=pad(10),
                                  font=(font_family, fs(10)),
                                  bg=colors['bg_card'],
                                  fg=colors['text_primary'])
    content_frame.pack(fill="both", expand=True, padx=pad(20), pady=pad(10))

    content_row = tk.Frame(content_frame, bg=colors['bg_card'])
    content_row.pack(fill="both", expand=True)
    content_text = tk.Text(content_row, wrap="char", height=15,
                           font=(font_family, fs(10)),
                           bg=colors['bg_card'], fg=colors['text_primary'],
                           padx=pad(12), pady=pad(12),
                           spacing1=0, spacing2=1, spacing3=2,
                           selectbackground=colors['primary'],
                           borderwidth=0, highlightthickness=0,
                           relief='flat')

    # Markdown 渲染（与主界面版本历史共用同一 helper）
    render_changelog_text(
        content_text, body, colors, font_family, font_family_bold,
        font_scale, layout_scale, section_font_size=11, item_font_size=10)

    content_text.config(state="disabled")
    content_scrollbar = ttk.Scrollbar(
        content_row,
        orient="vertical",
        command=content_text.yview,
    )
    content_text.configure(yscrollcommand=content_scrollbar.set)
    content_text.pack(side="left", fill="both", expand=True)
    content_scrollbar.pack(side="right", fill="y")

    # 进度条（初始隐藏）
    progress_frame = tk.Frame(dialog, bg=colors['bg_card'])
    progress_status_row = tk.Frame(progress_frame, bg=colors['bg_card'])
    progress_status_row.pack(fill="x")
    progress_label = tk.Label(
        progress_status_row,
        text="正在准备下载…",
        font=(font_family, fs(10)),
        bg=colors['bg_card'],
        fg=colors['text_primary'],
        anchor="w",
    )
    progress_label.pack(side="left", padx=pad(5))

    progress_bar = ttk.Progressbar(
        progress_status_row,
        length=int(200 * layout_scale),
        mode='determinate',
    )
    progress_bar.pack(side="left", padx=pad(5))
    progress_detail_label = tk.Label(
        progress_frame,
        text="",
        font=(font_family, fs(9)),
        bg=colors['bg_card'],
        fg=colors['text_secondary'],
        justify="left",
        anchor="w",
        wraplength=max(pad(360), dw - pad(80)),
    )

    # 按钮框
    button_frame = tk.Frame(dialog, bg=colors['bg_card'])
    button_frame.pack(pady=pad(20))
    update_state = {
        "running": False,
        "failed": False,
        "downloaded_path": None,
    }

    def on_cancel():
        if on_defer and not update_state["failed"]:
            on_defer()
        dialog.destroy()

    def show_update_details():
        """下载完成后显示可滚动的新版本升级明细。"""
        details = tk.Toplevel(dialog)
        details.title(f"v{result['latest']} 升级内容")
        details.transient(dialog)
        details.resizable(True, True)
        details.configure(bg=colors['bg_card'])
        details.minsize(int(520 * layout_scale), int(360 * height_scale))
        _place_dialog_centered(
            details,
            dialog,
            int(640 * layout_scale),
            int(460 * height_scale),
        )

        def close_details():
            try:
                details.grab_release()
            except tk.TclError:
                pass
            details.destroy()
            try:
                if dialog.winfo_exists():
                    dialog.grab_set()
            except tk.TclError:
                pass

        tk.Label(
            details,
            text=f"v{result['latest']} 升级明细",
            font=(font_family_bold, fs(13)),
            bg=colors['bg_card'],
            fg=colors['text_primary'],
        ).pack(pady=(pad(18), pad(10)))

        ttk.Button(
            details,
            text="关闭",
            command=close_details,
            width=12,
        ).pack(side="bottom", pady=(pad(8), pad(18)))

        text_frame = tk.Frame(details, bg=colors['bg_card'])
        text_frame.pack(
            fill="both",
            expand=True,
            padx=pad(20),
            pady=(0, pad(12)),
        )
        details_text = tk.Text(
            text_frame,
            wrap="char",
            font=(font_family, fs(10)),
            bg=colors['bg_card'],
            fg=colors['text_primary'],
            padx=pad(12),
            pady=pad(12),
            borderwidth=0,
            highlightthickness=0,
            relief="flat",
        )
        render_changelog_text(
            details_text,
            body,
            colors,
            font_family,
            font_family_bold,
            font_scale,
            layout_scale,
            section_font_size=11,
            item_font_size=10,
        )
        details_text.configure(state="disabled")
        details_scrollbar = ttk.Scrollbar(
            text_frame,
            orient="vertical",
            command=details_text.yview,
        )
        details_text.configure(yscrollcommand=details_scrollbar.set)
        details_text.pack(side="left", fill="both", expand=True)
        details_scrollbar.pack(side="right", fill="y")

        details.protocol("WM_DELETE_WINDOW", close_details)
        details.bind("<Escape>", lambda _event: close_details())
        details.grab_set()

    def show_update_failure(headline, message, detail=None):
        """Keep update failures actionable inside the existing update window."""
        try:
            if not dialog.winfo_exists():
                return
        except tk.TclError:
            return

        update_state.update(running=False, failed=True)
        progress_bar.configure(value=0)
        progress_label.configure(
            text=headline,
            fg=colors.get('danger_text', ui_theme.DANGER_TEXT),
        )
        detail_text = str(detail or "").strip()
        if detail_text:
            if len(detail_text) > 220:
                detail_text = detail_text[:219] + "…"
            message = f"{message}\n详细信息：{detail_text}"
        progress_detail_label.configure(text=message)
        if not progress_detail_label.winfo_manager():
            progress_detail_label.pack(fill="x", padx=pad(5), pady=(pad(5), 0))
        update_btn.configure(
            text="重试更新",
            command=on_update,
            state="normal",
        )
        cancel_btn.configure(
            text="关闭",
            command=on_cancel,
            state="normal",
        )
        if not button_frame.winfo_manager():
            button_frame.pack(pady=(pad(8), pad(20)))

    def install_downloaded_update():
        """再次校验缓存包后，启动独立的 Windows 安装进度窗口。"""
        downloaded_path = update_state.get("downloaded_path")
        if not downloaded_path:
            show_update_failure(
                "安装包不可用",
                "没有找到已下载的新版本，请重新下载。",
            )
            return

        cached_exe = Path(downloaded_path)
        asset_info = result.get("asset_info") or {}
        verified, verify_error = verify_downloaded_file(cached_exe, asset_info)
        if not verified:
            update_state["downloaded_path"] = None
            show_update_failure(
                "安装包不可用",
                "已保存的更新包未通过完整性校验，请重新下载。",
                verify_error,
            )
            return

        update_state.update(running=True, failed=False)
        button_frame.pack_forget()
        progress_label.configure(text="正在打开安装进度窗口…")
        dialog.update_idletasks()
        success, error = update_windows(
            str(cached_exe),
            sys.executable,
            source=source,
            asset_info=asset_info,
            old_version=result["current"],
        )
        if success:
            update_state["downloaded_path"] = None
            dialog.destroy()
            exit_for_update(root)
        else:
            show_update_failure(
                "安装未完成",
                "新版本已下载，但独立安装窗口未能启动，请稍后重试。",
                error,
            )

    def show_download_complete_actions(downloaded_path):
        """切换到可查看明细或立即安装的下载完成状态。"""
        update_state.update(
            running=False,
            failed=False,
            downloaded_path=str(downloaded_path),
        )
        content_frame.pack_forget()
        button_frame.pack_forget()
        if not progress_frame.winfo_manager():
            progress_frame.pack(fill="x", padx=pad(20), pady=(0, pad(12)))
        dialog.resizable(False, False)
        dialog.minsize(
            int(560 * layout_scale),
            int(230 * height_scale),
        )
        _place_dialog_centered(
            dialog,
            root,
            int(600 * layout_scale),
            int(260 * height_scale),
        )
        progress_bar.configure(value=100)
        progress_label.configure(text="下载完成，新版本已准备就绪")
        progress_detail_label.configure(
            text="安装包已保留；可先查看升级内容，稍后重新打开应用也无需再次下载。"
        )
        if not progress_detail_label.winfo_manager():
            progress_detail_label.pack(
                fill="x",
                padx=pad(5),
                pady=(pad(5), 0),
            )
        cancel_btn.configure(
            text="升级内容",
            command=show_update_details,
            state="normal",
        )
        update_btn.configure(
            text="立即安装",
            command=install_downloaded_update,
            state="normal",
        )
        button_frame.pack(pady=(pad(8), pad(20)))
        update_btn.focus_set()

    def on_update():
        """执行更新"""
        update_state.update(running=True, failed=False)
        progress_bar.configure(value=0)
        progress_label.configure(
            text="正在准备下载…",
            fg=colors['text_primary'],
        )
        progress_detail_label.configure(text="")
        if progress_detail_label.winfo_manager():
            progress_detail_label.pack_forget()
        button_frame.pack_forget()
        progress_frame.pack(fill="x", padx=pad(20), pady=(0, pad(12)))

        def do_update():
            if sys.platform == 'win32':
                cached_exe = _get_cached_windows_update(result)
                if cached_exe:
                    root.after(
                        0,
                        lambda path=cached_exe: show_download_complete_actions(path),
                    )
                    return

                download_url = result['download_url']
                if not download_url:
                    root.after(
                        0,
                        lambda: show_update_failure(
                            "暂时无法更新",
                            "版本信息中没有 Windows 安装包，请稍后重试或手动下载。",
                        ),
                    )
                    return

                try:
                    cache_dir = _windows_update_cache_dir(result["latest"])
                    cache_dir.mkdir(parents=True, exist_ok=True)
                except (OSError, ValueError) as cache_error:
                    root.after(
                        0,
                        lambda failure=cache_error: show_update_failure(
                            "暂时无法更新",
                            "无法创建本机更新缓存目录，请检查磁盘权限后重试。",
                            failure,
                        ),
                    )
                    return
                cached_exe = _windows_update_cache_path(result["latest"])
                partial_exe = cache_dir / "BOSS_ResumeFilter_new.part.exe"

                def progress_callback(downloaded, total):
                    if total > 0:
                        percent = int(downloaded / total * 100)
                        root.after(0, lambda: progress_bar.config(value=percent))
                        root.after(0, lambda: progress_label.config(
                            text=f"下载中... {percent}%"))

                asset_info = result.get('asset_info', {})
                success, error = download_and_verify_file(
                    str(download_url), partial_exe, asset_info, progress_callback)

                if not success:
                    fallback_url = result.get('download_url_fallback')
                    if fallback_url and str(fallback_url) != str(download_url):
                        root.after(0, lambda: progress_label.config(
                            text="Gitee 下载失败，尝试 GitHub..."))
                        success, error = download_and_verify_file(
                            str(fallback_url), partial_exe, asset_info, progress_callback)
                    if not success:
                        root.after(
                            0,
                            lambda failure=error: show_update_failure(
                                "下载未完成",
                                "未能下载并校验新版本，请检查网络连接后重试。",
                                failure,
                            ),
                        )
                        return

                try:
                    os.replace(partial_exe, cached_exe)
                except OSError as cache_error:
                    root.after(
                        0,
                        lambda failure=cache_error: show_update_failure(
                            "下载未完成",
                            "更新包已经下载，但无法保存到本机缓存目录。",
                            failure,
                        ),
                    )
                    return

                root.after(
                    0,
                    lambda path=cached_exe: show_download_complete_actions(path),
                )

            else:
                if getattr(sys, 'frozen', False):
                    download_url = result['download_url']
                    if not download_url:
                        root.after(
                            0,
                            lambda: show_update_failure(
                                "暂时无法更新",
                                "版本信息中没有 macOS 安装包，请稍后重试或手动下载。",
                            ),
                        )
                        return

                    temp_dir = Path(tempfile.mkdtemp(prefix="boss_update_download_"))
                    temp_zip = temp_dir / "BOSS_ResumeFilter_mac.zip"

                    def progress_callback(downloaded, total):
                        if total > 0:
                            percent = int(downloaded / total * 100)
                            root.after(0, lambda: progress_bar.config(value=percent))
                            root.after(0, lambda: progress_label.config(
                                text=f"下载中... {percent}%"))

                    asset_info = result.get('asset_info', {})
                    success, error = download_and_verify_file(
                        str(download_url), temp_zip, asset_info, progress_callback)

                    if not success:
                        fallback_url = result.get('download_url_fallback')
                        if fallback_url and str(fallback_url) != str(download_url):
                            root.after(0, lambda: progress_label.config(
                                text="Gitee 下载失败，尝试 GitHub..."))
                            success, error = download_and_verify_file(
                                str(fallback_url), temp_zip, asset_info, progress_callback)
                        if not success:
                            root.after(
                                0,
                                lambda failure=error: show_update_failure(
                                    "下载未完成",
                                    "未能下载并校验新版本，请检查网络连接后重试。",
                                    failure,
                                ),
                            )
                            return

                    root.after(0, lambda: progress_label.config(text="正在安装..."))
                    exe_path = Path(sys.executable).resolve()
                    current_app = exe_path
                    while current_app.suffix != '.app' and current_app != current_app.parent:
                        current_app = current_app.parent

                    if current_app.suffix != '.app':
                        root.after(
                            0,
                            lambda: show_update_failure(
                                "安装未完成",
                                "无法识别当前应用的安装位置，请从发布页手动下载安装包。",
                            ),
                        )
                        return

                    success, message = update_macos_app(
                        str(temp_zip), str(current_app))
                    if success:
                        root.after(0, lambda: (
                            progress_label.config(text=message),
                            dialog.destroy(),
                            exit_for_update(root)
                        ))
                    else:
                        root.after(
                            0,
                            lambda failure=message: show_update_failure(
                                "安装未完成",
                                "新版本已下载，但无法替换当前应用，请关闭可能占用程序文件的工具后重试。",
                                failure,
                            ),
                        )
                else:
                    success, message = update_macos()
                    if success:
                        root.after(0, lambda: (
                            messagebox.show_result(
                                "更新成功",
                                headline="应用更新已完成",
                                message=message,
                                notice="请手动重启应用以使用新版本。",
                                parent=dialog),
                            dialog.destroy()
                        ))
                    else:
                        root.after(
                            0,
                            lambda failure=message: show_update_failure(
                                "更新未完成",
                                "自动更新没有执行成功，请根据详细信息处理后重试。",
                                failure,
                            ),
                        )

        threading.Thread(target=do_update, daemon=True).start()

    # 按钮（复用主应用 ttk 按钮体系，与全局样式一致）
    cancel_btn = ttk.Button(
        button_frame,
        text="稍后更新",
        command=on_cancel,
        style='TButton',
        width=12,
        cursor='hand2'
    )
    cancel_btn.pack(side="left", padx=pad(6))

    update_btn = ttk.Button(
        button_frame,
        text="立即更新",
        command=on_update,
        style='Accent.TButton',
        width=12,
        cursor='hand2'
    )
    update_btn.pack(side="left", padx=pad(6))

    dialog.bind('<Escape>', lambda e: on_cancel())
    dialog.protocol("WM_DELETE_WINDOW", on_cancel)

    cached_update_path = result.get("cached_update_path")
    if sys.platform == "win32" and cached_update_path:
        try:
            cached_update_path = Path(cached_update_path).resolve()
            expected_cache_path = _windows_update_cache_path(
                result["latest"]
            ).resolve()
            if (
                cached_update_path == expected_cache_path
                and cached_update_path.is_file()
            ):
                root.after(
                    0,
                    lambda path=cached_update_path: show_download_complete_actions(path),
                )
        except (OSError, ValueError):
            pass


def _read_cooldown(base_dir: Path) -> dict:
    """读取更新检查冷却状态，返回 {timestamp, result, fail_count}。

    兼容旧版纯时间戳格式（自动升级）。文件不存在或损坏时返回空状态。
    """
    cooldown_file = base_dir / ".last_update_check"
    if not cooldown_file.exists():
        return {"timestamp": 0, "result": None, "fail_count": 0}

    try:
        content = cooldown_file.read_text().strip()
        state = json.loads(content)
        if isinstance(state, dict):
            return {
                "timestamp": state.get("timestamp", 0),
                "result": state.get("result"),
                "fail_count": state.get("fail_count", 0),
            }
    except (json.JSONDecodeError, OSError):
        pass

    # 旧版格式：纯时间戳，自动升级
    try:
        return {
            "timestamp": float(cooldown_file.read_text().strip()),
            "result": None,
            "fail_count": 0,
        }
    except (ValueError, OSError):
        return {"timestamp": 0, "result": None, "fail_count": 0}


def _write_cooldown(base_dir: Path, result: str, fail_count: int = 0) -> None:
    """写入更新检查冷却状态。"""
    cooldown_file = base_dir / ".last_update_check"
    state = {"timestamp": time.time(), "result": result, "fail_count": fail_count}
    try:
        cooldown_file.write_text(json.dumps(state))
    except OSError:
        pass


def _adaptive_cooldown(result: str, fail_count: int) -> float:
    """计算自适应冷却时间（秒）。

    - 发现新版本: 24h（用户已看到弹窗，避免重复打扰）
    - 无更新: 4h
    - 检查失败: 15min 起，指数退避（30min → 1h）
    """
    if result == "found":
        return 24 * 3600
    if result == "no_update":
        return 4 * 3600
    # result == "failed": 指数退避
    return 900 * (2 ** min(fail_count, 2))


def _write_update_defer_cooldown(base_dir: Path) -> None:
    """用户明确选择稍后提醒后，写入发现新版本冷却。"""
    _write_cooldown(base_dir, "found", 0)


def auto_check_on_startup(root, delay_ms=3000, gui=None):
    """
    启动时自动检查更新（延迟执行），自适应冷却机制

    Args:
        root: tkinter 根窗口
        delay_ms: 延迟毫秒数（默认 3 秒，避免启动时卡顿）
        gui: BossFilterGUI 实例（用于字体缩放和配色）
    """
    base_dir = get_base_dir()
    state = _read_cooldown(base_dir)

    hours_since = (time.time() - state["timestamp"]) / 3600
    cooldown_hours = _adaptive_cooldown(state["result"], state["fail_count"]) / 3600

    if hours_since < cooldown_hours:
        return

    def _do_check_and_record():
        """执行检查并记录结果"""
        def record_result(result):
            if result.get("error"):
                _write_cooldown(base_dir, "failed", state["fail_count"] + 1)
            elif not result.get("has_update"):
                _write_cooldown(base_dir, "no_update", 0)

        check_and_update_gui(
            root,
            silent=True,
            on_complete=record_result,
            gui=gui,
            source="startup",
            on_defer=lambda: _write_update_defer_cooldown(base_dir),
        )

    root.after(delay_ms, _do_check_and_record)


if __name__ == "__main__":
    # 测试
    print("测试更新检查...")
    result = check_github_release()
    print(f"当前版本: {result['current']}")
    print(f"最新版本: {result['latest']}")
    print(f"有更新: {result['has_update']}")
    if result['error']:
        print(f"错误: {result['error']}")
    if result['download_url']:
        print(f"下载链接: {result['download_url']}")
