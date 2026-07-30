"""
BOSS 简历筛选器 - 打包脚本
用法：
  python build.py --check                仅执行发布前检查，不打包、不提交、不推送
  python build.py --user-audit           用户视角发布审计，不打包、不提交、不推送
  python build.py --check --strict-changelog  启用严格 CHANGELOG/README/latest.json 文案门禁
  python build.py                      仅打包 + 版本核对
  python build.py --ci --release       GitHub Actions 单平台构建（内部使用）
  python scripts/release_dispatch.py ... 本机授权后启动正式发布
  python build.py --github-upload X.Y.Z 手动补传产物到 GitHub Release
  python build.py --gitee-upload X.Y.Z 手动补传产物到 Gitee Release
  python build.py --verify-gitee-integrity X.Y.Z 严格回下载校验 Gitee SHA256
  python build.py --verify-release X.Y.Z 只读核验双远端、Release 与 latest.json
"""
import argparse
import ast
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

import requests

from release_user_audit import audit_user_facing_release, summarize_release_user_audit

_BUILD_SCRIPTS_DIR = Path(__file__).resolve().parent / "scripts"
if str(_BUILD_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_BUILD_SCRIPTS_DIR))
import release_retry  # noqa: E402

# Windows 终端默认 GBK 编码导致中文乱码和 Unicode 字符崩溃，强制 UTF-8
if sys.platform == 'win32':
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except (AttributeError, OSError):
        pass  # Python < 3.7 或不支持 reconfigure

# Windows 终端启用 ANSI 转义码（光标控制、颜色），用于实时进度表重绘
if sys.platform == 'win32':
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        kernel32.SetConsoleMode(kernel32.GetStdHandle(-11), 7)
    except Exception:
        pass
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


class ReleaseProgress:
    """实时进度表：每次状态变化时重绘整张表，用户一目了然。"""

    def __init__(self, version, step_names):
        self.version = version
        self.step_names = step_names
        self.steps = [{'status': 'pending', 'duration': None, 'subs': []} for _ in step_names]
        self.current = -1
        self.start_time = time.time()
        self._lines_printed = 0
        # 检测终端是否支持 ANSI
        self._ansi = hasattr(sys.stdout, 'isatty') and sys.stdout.isatty()
        self._progress_file = BASE_DIR / '.build_progress.json'
        self._write_progress_file()

    def _write_progress_file(self):
        """写入进度状态到固定路径的 JSON 文件，供外部查询。"""
        from datetime import datetime
        data = {
            'version': self.version,
            'started_at': datetime.fromtimestamp(self.start_time).isoformat(timespec='seconds'),
            'elapsed': round(time.time() - self.start_time, 1),
            'current_step': self.current,
            'steps': [
                {
                    'name': name,
                    'status': step['status'],
                    'duration': round(step['duration'], 1) if step['duration'] is not None else None,
                    'sub_count': len(step['subs']),
                    'last_sub': step['subs'][-1]['msg'] if step['subs'] else None,
                }
                for name, step in zip(self.step_names, self.steps)
            ]
        }
        try:
            self._progress_file.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
        except Exception:
            pass  # 进度文件写入失败不影响发布流程

    def start_step(self, idx):
        """开始第 idx 步（0-based）"""
        self.current = idx
        self.steps[idx]['status'] = 'running'
        self.steps[idx]['start'] = time.time()
        self._write_progress_file()
        if not self._ansi:
            print(f"[{idx+1}/{len(self.step_names)}] {self.step_names[idx]}...", flush=True)
        self._render()

    def end_step(self):
        """结束当前步骤"""
        step = self.steps[self.current]
        step['status'] = 'done'
        step['duration'] = time.time() - step['start']
        self._write_progress_file()
        if not self._ansi:
            print(f"[{self.current+1}/{len(self.step_names)}] ✓ {self.step_names[self.current]} ({self._fmt_duration(step['duration'])})", flush=True)
        self._render()

    def sub(self, msg):
        """当前步骤的子步骤输出（带时间戳，用于汇总表计算耗时）"""
        if self.current >= 0:
            self.steps[self.current]['subs'].append({'msg': msg, 'time': time.time()})
        self._write_progress_file()
        if not self._ansi:
            print(f"  │ {msg}", flush=True)
        self._render()

    def skip_step(self, idx, reason=''):
        """跳过某步骤"""
        self.steps[idx]['status'] = 'skipped'
        self.steps[idx]['duration'] = 0
        if reason:
            self.steps[idx]['subs'].append({'msg': reason, 'time': time.time()})
        self._write_progress_file()
        if not self._ansi:
            print(f"[{idx+1}/{len(self.step_names)}] – {self.step_names[idx]} 跳过", flush=True)
        self._render()

    def fail_step(self, msg=''):
        """当前步骤失败"""
        step = self.steps[self.current]
        step['status'] = 'failed'
        step['duration'] = time.time() - step.get('start', time.time())
        if msg:
            step['subs'].append({'msg': msg, 'time': time.time()})
        self._write_progress_file()
        if not self._ansi:
            print(f"[{self.current+1}/{len(self.step_names)}] ✗ {self.step_names[self.current]}: {msg}", flush=True)
        self._render()

    def _fmt_duration(self, d):
        if d is None:
            return '—'
        if d < 1:
            return f'{d:.1f}s'
        if d < 60:
            return f'{d:.0f}s'
        m, s = divmod(int(d), 60)
        return f'{m}m{s}s'

    def _render(self):
        """重绘整张进度表"""
        if not self._ansi:
            return  # 非终端环境不重绘，由调用方 fallback 打印
        # 光标上移到表头位置
        if self._lines_printed > 0:
            sys.stdout.write(f'\033[{self._lines_printed}A\033[J')
        lines = self._build_lines()
        for line in lines:
            sys.stdout.write(line + '\n')
        sys.stdout.flush()
        self._lines_printed = len(lines)

    def _build_lines(self):
        from datetime import datetime
        W = 60
        elapsed = time.time() - self.start_time
        lines = []
        # 表头
        lines.append('═' * W)
        ts = datetime.now().strftime('%H:%M:%S')
        lines.append(f'  Release v{self.version}  ·  已开始 {ts}  ·  已用 {self._fmt_duration(elapsed)}')
        lines.append('─' * W)
        # 步骤
        for i, (name, step) in enumerate(zip(self.step_names, self.steps)):
            status = step['status']
            dur = self._fmt_duration(step.get('duration'))
            if status == 'done':
                icon = '✓'
                dur_str = dur.rjust(6)
                lines.append(f'  [{i+1}/{len(self.step_names)}] {icon} {name:<24s} {dur_str}')
            elif status == 'running':
                running_elapsed = time.time() - step.get('start', time.time())
                icon = '▶'
                dur_str = self._fmt_duration(running_elapsed).rjust(6)
                lines.append(f'  [{i+1}/{len(self.step_names)}] {icon} {name:<24s} {dur_str}  ← 进行中')
                # 显示最近 3 条子步骤
                for sub in step['subs'][-3:]:
                    lines.append(f'        │ {sub["msg"]}')
            elif status == 'skipped':
                icon = '–'
                lines.append(f'  [{i+1}/{len(self.step_names)}] {icon} {name:<24s} 跳过')
            elif status == 'failed':
                icon = '✗'
                dur_str = dur.rjust(6)
                lines.append(f'  [{i+1}/{len(self.step_names)}] {icon} {name:<24s} {dur_str}')
                for sub in step['subs'][-2:]:
                    lines.append(f'        │ {sub["msg"]}')
            else:
                lines.append(f'  [{i+1}/{len(self.step_names)}]   {name:<24s} —')
        lines.append('─' * W)
        return lines

    def render_final(self, artifact_path, size_mb):
        """最终汇总表（不再重绘，直接追加）"""
        W = 60
        total = time.time() - self.start_time
        # 先清除进度表区域
        if self._ansi and self._lines_printed > 0:
            sys.stdout.write(f'\033[{self._lines_printed}A\033[J')
            sys.stdout.flush()
        lines = []
        lines.append('═' * W)
        lines.append(f'  ✓ v{self.version} 发布完成')
        lines.append('═' * W)
        lines.append(f'  产物: {artifact_path.name} ({size_mb:.1f} MB)')
        lines.append('')

        # 表格格式的步骤耗时（CJK 字符算 2 列，ASCII 算 1 列）
        import unicodedata
        def _dw(s):
            """字符串的显示宽度（使用 East Asian Width 标准）"""
            w = 0
            for ch in str(s):
                eaw = unicodedata.east_asian_width(ch)
                w += 2 if eaw in ('F', 'W') else 1
            return w

        def _pad(s, width):
            """按显示宽度右填充空格"""
            s = str(s)
            return s + ' ' * max(0, width - _dw(s))

        COL_IDX = 4    # " 1. "
        COL_NAME = 20  # 任务名称
        COL_STAT = 4   # 状态
        COL_DUR = 12   # 耗时

        lines.append(f'  {_pad("序号", COL_IDX)} {_pad("任务名称", COL_NAME)} {_pad("状态", COL_STAT)} {_pad("耗时", COL_DUR)}')
        lines.append('  ' + '─' * (COL_IDX + COL_NAME + COL_STAT + COL_DUR + 3))
        for idx, (name, step) in enumerate(zip(self.step_names, self.steps), 1):
            dur = self._fmt_duration(step.get('duration'))
            status = step['status']
            if status == 'done':
                icon = '✓'
                lines.append(f'  {_pad(f"{idx}.", COL_IDX)} {_pad(name, COL_NAME)} {_pad(icon, COL_STAT)} {dur}')
            elif status == 'skipped':
                icon = '–'
                lines.append(f'  {_pad(f"{idx}.", COL_IDX)} {_pad(name, COL_NAME)} {_pad(icon, COL_STAT)} 跳过')
            elif status == 'failed':
                icon = '✗'
                lines.append(f'  {_pad(f"{idx}.", COL_IDX)} {_pad(name, COL_NAME)} {_pad(icon, COL_STAT)} 失败')
            else:
                lines.append(f'  {_pad(f"{idx}.", COL_IDX)} {_pad(name, COL_NAME)} {" " * COL_STAT} {dur}')

            # 子步骤：从 subs 中提取阶段边界，计算耗时
            subs = step.get('subs', [])
            if len(subs) >= 3:
                # 过滤掉细节日志和状态消息
                skip_prefixes = ('[OK]', '[跳过]', '[更新]', '[重试]', '[失败]', '[信息]')
                skip_contains = ('已存在', '已就绪', '已推送', '已同步', '已上传', '已删除', '已完成', '发布完成')
                phases = []
                for s in subs:
                    msg = s['msg'] if isinstance(s, dict) else s
                    if any(msg.startswith(p) for p in skip_prefixes):
                        continue
                    if any(kw in msg for kw in skip_contains):
                        continue
                    phases.append(s)
                # 计算各阶段耗时
                if len(phases) >= 2:
                    step_start = step.get('start', phases[0]['time'] if isinstance(phases[0], dict) else 0)
                    step_end = step_start + (step.get('duration') or 0)
                    SUB_NAME_W = 28  # 子步骤名称列宽
                    for j, phase in enumerate(phases):
                        t = phase['time'] if isinstance(phase, dict) else step_start
                        msg = phase['msg'] if isinstance(phase, dict) else phase
                        if j + 1 < len(phases):
                            next_t = phases[j+1]['time'] if isinstance(phases[j+1], dict) else step_end
                            phase_dur = next_t - t
                        else:
                            phase_dur = step_end - t
                        sub_label = f"{idx}.{j+1}"
                        # 精简名称：去掉末尾省略号，截断
                        clean = msg.rstrip('.').rstrip('。')
                        max_w = SUB_NAME_W - 2
                        while _dw(clean) > max_w and len(clean) > 4:
                            clean = clean[:-1]
                        sub_name = f"  {clean}"
                        lines.append(f'  {_pad(sub_label, COL_IDX)} {_pad(sub_name, SUB_NAME_W)} {"":{COL_STAT}} {self._fmt_duration(phase_dur)}')

        lines.append('  ' + '─' * (COL_IDX + COL_NAME + COL_STAT + COL_DUR + 3))
        m, s = divmod(int(total), 60)
        total_str = f'{total:.1f}s ({m}m{s:02d}s)'
        lines.append(f'  {_pad("", COL_IDX)} {_pad("总耗时", COL_NAME)} {" " * COL_STAT} {total_str}')
        lines.append('═' * W)
        print('\n'.join(lines))

        # 写入最终完成状态到进度文件
        from datetime import datetime as _dt
        try:
            data = {
                'status': 'completed',
                'version': self.version,
                'completed_at': _dt.now().isoformat(timespec='seconds'),
                'total_duration': round(total, 1),
                'artifact': artifact_path.name,
                'size_mb': round(size_mb, 1),
                'steps': [
                    {'name': name, 'status': step['status'],
                     'duration': round(step['duration'], 1) if step['duration'] else None}
                    for name, step in zip(self.step_names, self.steps)
                ]
            }
            self._progress_file.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
        except Exception:
            pass

BASE_DIR = Path(__file__).parent.resolve()
DIST_DIR = BASE_DIR / "dist"
BUILD_DIR = BASE_DIR / "build"
VENV_DIR = BASE_DIR / "pack_venv"
BUILD_STATE_PATH = BASE_DIR / ".build_state.json"
BUILD_FINGERPRINT_VERSION = 1
LARGE_TRANSFER_THRESHOLD = 20 * 1024 * 1024
SMALL_TRANSFER_WORKERS = 3
TRANSFER_TIMEOUT_SECONDS = 600

# 平台检测
IS_MAC = sys.platform == 'darwin'
IS_WIN = sys.platform == 'win32'
SEP = ':' if IS_MAC else ';'  # PyInstaller --add-data 分隔符

# 虚拟环境 Python 路径（按平台）
if IS_MAC:
    VENV_PYTHON = VENV_DIR / "bin" / "python"
else:
    VENV_PYTHON = VENV_DIR / "Scripts" / "python.exe"
SENSITIVE_TRACKED_PATHS = [
    ".env",
    "candidates_all.json",
    "candidates_all.xlsx",
]
SOURCE_CHECK_FILES = [
    "bossmaster.py",
    "gui_main.py",
    "filtering.py",
    "storage.py",
    "safe_json_store.py",
    "job_config_store.py",
    "job_identity.py",
    "llm_eval.py",
    "ai_adapter.py",
    "job_ai_parser.py",
    "job_config_diagnostics.py",
    "doc_parser.py",
    "security.py",
    "build.py",
    "icons.py",
    "migrate_keys.py",
    "tests/run_unit_tests.py",
    "tests/test_import.py",
    "tests/unit/test_core_logic.py",
    "tests/unit/test_llm_eval.py",
    "tests/unit/test_ai_adapter.py",
    "tests/unit/test_job_ai_parser.py",
    "tests/unit/test_job_config_diagnostics.py",
    "tests/unit/test_selectors.py",
]


def _find_conda_tcl_tk():
    """Return Anaconda Tcl/Tk paths when the pack venv is based on conda Python."""
    # macOS 上 Homebrew Python 的 Tcl/Tk 由 PyInstaller 自动收集，无需手动指定
    if IS_MAC:
        return None

    base_prefix = Path(sys.base_prefix).resolve()
    tkinter_dir = base_prefix / "Lib" / "tkinter"
    tkinter_pyd = base_prefix / "DLLs" / "_tkinter.pyd"
    lib_dir = base_prefix / "Library" / "lib"
    bin_dir = base_prefix / "Library" / "bin"
    tcl_dir = lib_dir / "tcl8.6"
    tk_dir = lib_dir / "tk8.6"
    tcl_dll = bin_dir / "tcl86t.dll"
    tk_dll = bin_dir / "tk86t.dll"

    paths = {
        "tkinter_dir": tkinter_dir,
        "tkinter_pyd": tkinter_pyd,
        "tcl_dir": tcl_dir,
        "tk_dir": tk_dir,
        "tcl_dll": tcl_dll,
        "tk_dll": tk_dll,
    }
    if all(path.exists() for path in paths.values()):
        return paths
    return None


def _check_tkinter_packaging_support():
    """Fail before PyInstaller if Tcl/Tk cannot be located for a Tkinter GUI build."""
    try:
        import tkinter  # noqa: F401
        import tkinter.ttk  # noqa: F401
        import tkinter.font  # noqa: F401
        import tkinter.filedialog  # noqa: F401
        import tkinter.messagebox  # noqa: F401
    except ImportError as e:
        print(f"[错误] tkinter 导入失败：{e}")
        sys.exit(1)

    tcl_tk = _find_conda_tcl_tk()
    if tcl_tk:
        print("  [OK] Tcl/Tk 运行库已定位")
        return

    # Non-conda Python distributions normally let PyInstaller's tkinter hook
    # find Tcl/Tk automatically. Keep the check permissive outside conda.
    print("  [跳过] 未检测到 Anaconda Tcl/Tk 布局，交给 PyInstaller 自动收集")


def _pyinstaller_tk_args():
    """Return PyInstaller arguments and environment needed for Tkinter bundling."""
    tcl_tk = _find_conda_tcl_tk()
    if not tcl_tk:
        return [], os.environ.copy()

    env = os.environ.copy()
    env["TCL_LIBRARY"] = str(tcl_tk["tcl_dir"])
    env["TK_LIBRARY"] = str(tcl_tk["tk_dir"])

    return [
        "--collect-submodules", "tkinter",
        "--add-data", f'{tcl_tk["tkinter_dir"]}{SEP}tkinter',
        "--add-binary", f'{tcl_tk["tkinter_pyd"]}{SEP}.',
        "--add-data", f'{tcl_tk["tcl_dir"]}{SEP}tcl/tcl8.6' if IS_MAC else f'{tcl_tk["tcl_dir"]}{SEP}tcl\\tcl8.6',
        "--add-data", f'{tcl_tk["tk_dir"]}{SEP}tcl/tk8.6' if IS_MAC else f'{tcl_tk["tk_dir"]}{SEP}tcl\\tk8.6',
        "--add-binary", f'{tcl_tk["tcl_dll"]}{SEP}.',
        "--add-binary", f'{tcl_tk["tk_dll"]}{SEP}.',
    ], env


def run_in_venv(entrypoint: str | Path | None = None) -> None:
    """如果在系统 Python 中运行，使用 pack_venv 重新启动指定入口。"""
    if Path(sys.executable).resolve() != VENV_PYTHON.resolve():
        if not VENV_PYTHON.exists():
            print(f"[错误] 虚拟环境不存在：{VENV_DIR}")
            if IS_MAC:
                print("请先创建：python3 -m venv pack_venv && source pack_venv/bin/activate && pip install -r requirements.txt pyinstaller")
            else:
                print("请先创建：python -m venv pack_venv && pack_venv\\Scripts\\activate && pip install -r requirements.txt pyinstaller")
            sys.exit(1)
        target = Path(entrypoint or __file__).resolve()
        print(f"[使用虚拟环境] {VENV_PYTHON}", flush=True)
        result = subprocess.run([str(VENV_PYTHON), str(target), *sys.argv[1:]])
        sys.exit(result.returncode)


def clean_dist():
    """清理旧的打包产物"""
    if IS_MAC:
        # macOS: 清理 .app bundle、PyInstaller 中间目录、.zip、.dmg
        app_path = DIST_DIR / "BOSS_ResumeFilter.app"
        collect_path = DIST_DIR / "BOSS_ResumeFilter"
        zip_path = DIST_DIR / "BOSS_ResumeFilter_mac.zip"
        dmg_path = DIST_DIR / "BOSS_ResumeFilter.dmg"

        for path in [app_path, collect_path]:
            if path.exists():
                shutil.rmtree(path)
                print(f"  删除旧目录: {path}")

        for path in [zip_path, dmg_path]:
            if path.exists():
                path.unlink()
                print(f"  删除旧文件: {path}")
    else:
        # Windows: 清理 .exe
        exe_path = DIST_DIR / "BOSS_ResumeFilter.exe"
        if exe_path.exists():
            for attempt in range(3):
                try:
                    exe_path.unlink()
                    print(f"  删除旧 EXE: {exe_path}")
                    return
                except PermissionError as e:
                    if attempt < 2:
                        print(f"  等待文件释放... ({attempt + 1}/3)")
                        time.sleep(2)
                    else:
                        print(f"[警告] 无法删除旧 EXE (可能被占用): {e}")
                        print("  请手动关闭占用进程后重试")
                        sys.exit(1)


def _create_mac_zip():
    """创建 macOS ZIP 包（用于自动更新）"""
    app_dir = DIST_DIR / "BOSS_ResumeFilter.app"
    zip_path = DIST_DIR / "BOSS_ResumeFilter_mac.zip"

    if not app_dir.exists():
        print(f"[错误] .app 不存在：{app_dir}")
        sys.exit(1)

    print(f"\n>>> 创建 ZIP 包...")

    if zip_path.exists():
        zip_path.unlink()

    # macOS .app bundles contain framework symlinks, executable bits, and
    # extended attributes. Python zipfile loses enough of that metadata to
    # make PyInstaller apps fail after auto-update, so use ditto here.
    subprocess.run(
        [
            "ditto",
            "-c",
            "-k",
            "--sequesterRsrc",
            "--keepParent",
            str(app_dir),
            str(zip_path),
        ],
        cwd=DIST_DIR,
        check=True,
    )

    size_mb = zip_path.stat().st_size / (1024 * 1024)
    print(f"  [OK] ZIP: {zip_path} ({size_mb:.1f} MB)")
    return zip_path


def _create_mac_dmg():
    """创建 macOS DMG 安装包（标准布局：.app 在左，Applications 在右）"""
    app_dir = DIST_DIR / "BOSS_ResumeFilter.app"
    dmg_path = DIST_DIR / "BOSS_ResumeFilter.dmg"

    if not app_dir.exists():
        print(f"[错误] .app 不存在：{app_dir}")
        sys.exit(1)

    print(f"\n>>> 创建 DMG 安装包...")

    try:
        import dmgbuild
    except ImportError:
        print("[错误] 缺少 dmgbuild 依赖，请运行：pip install dmgbuild")
        sys.exit(1)

    # 根据 .app 大小自动计算 DMG 容量（留 50MB 余量）
    app_size_mb = sum(f.stat().st_size for f in app_dir.rglob('*') if f.is_file()) / (1024 * 1024)
    dmg_size_mb = int(app_size_mb) + 50

    settings = {
        'format': 'UDBZ',
        'size': f'{dmg_size_mb}m',
        'files': [str(app_dir)],
        'symlinks': {'Applications': '/Applications'},
        'icon_locations': {
            'BOSS_ResumeFilter.app': (140, 200),
            'Applications': (500, 200),
        },
        'icon_size': 100,
        'window_rect': ((100, 100), (640, 480)),
        'include_iconview_settings': True,
        'default_view': 'icon-view',
        'show_icon_preview': False,
        'text_size': 13,
    }

    dmgbuild.build_dmg(
        filename=str(dmg_path),
        volume_name='BOSS简历筛选器',
        settings=settings,
    )

    size_mb = dmg_path.stat().st_size / (1024 * 1024)
    print(f"  [OK] DMG: {dmg_path} ({size_mb:.1f} MB)")
    return dmg_path


# PyPI 包名 → import 名 映射（部分包名与 import 名不同）
REQUIRED_IMPORTS = {
    "DrissionPage": "DrissionPage",
    "openpyxl": "openpyxl",
    "requests": "requests",
    "dotenv": "python-dotenv",
    "keyring": "keyring",
    "PIL": "Pillow",
    "tkcalendar": "tkcalendar",
    "pdfminer": "pdfminer.six",
    "docx": "python-docx",
    "striprtf": "striprtf",
}

PLATFORM_REQUIRED_IMPORTS = {
    "win32": {
        "win32ctypes.pywin32": "pywin32-ctypes",
    },
}


def _required_imports_for_current_platform():
    """Return common imports plus dependencies required by this OS."""
    imports = dict(REQUIRED_IMPORTS)
    imports.update(PLATFORM_REQUIRED_IMPORTS.get(sys.platform, {}))
    return imports


def _normalize_package_name(name):
    """Normalize package names for requirements/import-check comparison."""
    return re.sub(r"[-_.]+", "-", name).lower()


def _requirements_packages():
    """Return direct package names declared in requirements.txt."""
    packages = set()
    requirements_path = BASE_DIR / "requirements.txt"
    for raw_line in requirements_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or line.startswith("-"):
            continue
        match = re.match(r"([A-Za-z0-9_.-]+)", line)
        if match:
            packages.add(_normalize_package_name(match.group(1)))
    return packages


def _check_dependency_manifest_complete():
    """Ensure every requirements.txt package has an explicit import check."""
    requirements = _requirements_packages()
    checked = {
        _normalize_package_name(pkg_name)
        for pkg_name in REQUIRED_IMPORTS.values()
    }
    checked.update(
        _normalize_package_name(pkg_name)
        for platform_imports in PLATFORM_REQUIRED_IMPORTS.values()
        for pkg_name in platform_imports.values()
    )
    missing = sorted(requirements - checked)
    if missing:
        print("[错误] requirements.txt 中存在未纳入打包依赖检查的包：\n")
        for pkg_name in missing:
            print(f"  [X] {pkg_name}")
        print("\n请同步更新 build.py 的 REQUIRED_IMPORTS，避免漏装依赖后仍然打包。")
        sys.exit(1)


def _check_dependencies():
    """打包前验证所有关键依赖已安装，缺失时直接中断并给出修复命令"""
    _check_dependency_manifest_complete()

    missing = []
    for import_name, pkg_name in _required_imports_for_current_platform().items():
        try:
            __import__(import_name)
        except ImportError:
            missing.append((import_name, pkg_name))

    if missing:
        print("[依赖缺失] 以下包未安装：\n")
        for import_name, pkg_name in missing:
            print(f"  [X] {pkg_name}（import '{import_name}' 失败）")
        print(f"\n请在 pack_venv 中安装缺失依赖后重试：")
        print("  $env:PYTHONUTF8='1'; pack_venv\\Scripts\\pip install -r requirements.txt\n")
        sys.exit(1)

    print("  [OK] 依赖检查通过\n")


def _run_checked(cmd, description):
    """运行检查命令，失败时中断"""
    print(f">>> {description}")
    result = subprocess.run(cmd, cwd=BASE_DIR)
    if result.returncode != 0:
        print(f"[错误] {description} 失败")
        sys.exit(result.returncode)


def _git_output(args):
    """返回 git 命令 stdout"""
    result = subprocess.run(["git", *args], cwd=BASE_DIR, capture_output=True, text=True, check=True)
    return result.stdout.strip()


def _tracked_paths(paths):
    """返回仍被 Git 跟踪的路径"""
    tracked = []
    for path in paths:
        result = subprocess.run(["git", "ls-files", "--", path], cwd=BASE_DIR, capture_output=True, text=True)
        if result.stdout.strip():
            tracked.append(path)
    return tracked


def _check_storage_not_tracked():
    tracked_count = len(_git_output(["ls-files", ".storage"]).splitlines())
    if tracked_count:
        print(f"[错误] .storage 仍有 {tracked_count} 个文件被 Git 跟踪")
        print("请先执行：git rm -r --cached -- .storage")
        sys.exit(1)
    print("  [OK] .storage 未被 Git 跟踪")


def _check_sensitive_files_not_tracked():
    tracked = _tracked_paths(SENSITIVE_TRACKED_PATHS)
    if tracked:
        print("[错误] 以下本地敏感/运行数据仍被 Git 跟踪：")
        for path in tracked:
            print(f"  - {path}")
        sys.exit(1)
    print("  [OK] 敏感/运行数据未被 Git 跟踪")


def _check_api_config_has_no_plaintext_key():
    config_path = BASE_DIR / "api_config.json"
    if not config_path.exists():
        print("  [跳过] api_config.json 不存在")
        return
    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        print(f"[错误] api_config.json 不是合法 JSON：{e}")
        sys.exit(1)

    offenders = []
    if config.get("api_key"):
        offenders.append("api_key")
    for idx, model in enumerate(config.get("saved_models", [])):
        if isinstance(model, dict) and (model.get("api_key") or model.get("api_key_ref")):
            offenders.append(f"saved_models[{idx}]")

    if offenders:
        print("[错误] api_config.json 含明文或旧版 API Key 引用：")
        for item in offenders:
            print(f"  - {item}")
        sys.exit(1)
    print("  [OK] api_config.json 不含明文 API Key")


def _check_source_compiles():
    files = [str(BASE_DIR / path) for path in SOURCE_CHECK_FILES if (BASE_DIR / path).exists()]
    _run_checked([sys.executable, "-m", "py_compile", *files], "源码编译检查")


def _check_undefined_names():
    """Block releases on Ruff F821 undefined-name findings."""
    _run_checked(
        [
            sys.executable,
            "-m",
            "ruff",
            "check",
            ".",
            "--select",
            "F821",
            "--exclude",
            "pack_venv,.worktrees,build,dist,tests/archive",
        ],
        "Ruff F821 未定义名称检查",
    )


def _run_unit_checks():
    _run_checked([sys.executable, "tests/run_unit_tests.py"], "稳定单元回归")
    _run_checked([sys.executable, "tests/test_import.py"], "导入烟测")


def _run_preflight_step(label, func, *args, **kwargs):
    started = time.perf_counter()
    func(*args, **kwargs)
    elapsed = time.perf_counter() - started
    print(f"  [耗时] {label}: {elapsed:.1f}s")


def _check_worktree_clean():
    has_changes, status_text = _git_status()
    if has_changes:
        print("[错误] 工作区不干净，请先提交或撤销变更：\n")
        for line in status_text.splitlines():
            print(f"  {line}")
        sys.exit(1)
    print("  [OK] 工作区干净")


def _get_last_tag():
    """获取最近一个 git tag，供各检查函数复用。"""
    result = subprocess.run(
        ["git", "describe", "--tags", "--abbrev=0"],
        capture_output=True, text=True, cwd=BASE_DIR
    )
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def _check_changelog_updated():
    """检测核心代码有变更时 CHANGELOG.md 必须同步更新。"""
    last_tag = _get_last_tag()
    if not last_tag:
        print("  [跳过] CHANGELOG 检查：无法获取上一个 tag")
        return

    # 检查核心代码是否有变更
    core_files = [
        "gui_main.py", "gui_dialogs.py", "ui_messagebox.py", "changelog_parser.py", "bossmaster.py",
        "filtering.py", "storage.py", "llm_eval.py", "ai_adapter.py", "job_ai_parser.py",
        "job_config_diagnostics.py", "doc_parser.py", "security.py", "updater.py", "icons.py",
        "constants.py", "paths.py", "selectors.json", "ui_config.json",
        "job_config.json",
    ]
    result = subprocess.run(
        ["git", "diff", "--name-only", last_tag, "--"] + core_files,
        capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=BASE_DIR
    )
    changed_core = [f for f in result.stdout.strip().splitlines() if f]

    if not changed_core:
        print("  [OK] CHANGELOG 检查：核心代码无变更")
        return

    # 检查 CHANGELOG.md 是否更新
    result = subprocess.run(
        ["git", "diff", "--name-only", last_tag, "--", "CHANGELOG.md"],
        capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=BASE_DIR
    )
    changelog_changed = bool(result.stdout.strip())

    if not changelog_changed:
        print("[错误] 核心代码已变更但 CHANGELOG.md 未更新：\n")
        for f in changed_core:
            print(f"  - {f}")
        print("\n请先更新 CHANGELOG.md 再发布")
        sys.exit(1)

    print("  [OK] CHANGELOG 已同步更新")


CHANGELOG_REQUIRED_SECTIONS = ["新增功能", "体验优化", "问题修复"]


def _normalize_markdown(text):
    """Normalize markdown for release-note equality checks."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = [line.rstrip() for line in text.strip().splitlines()]
    return "\n".join(lines).strip()


def _parse_changelog_sections(body, heading_prefix="###"):
    """Return CHANGELOG/README release sections as {section_name: [bullet, ...]}."""
    sections = {}
    current_section = None
    heading_re = re.compile(rf"^{re.escape(heading_prefix)}\s+(.+?)\s*$")
    for line in body.splitlines():
        m_section = heading_re.match(line)
        if m_section:
            current_section = m_section.group(1)
            sections[current_section] = []
            continue
        if current_section and line.startswith("- "):
            sections[current_section].append(line.strip())
    return sections


def _parse_readme_release_sections(body):
    """Return README current-version summary sections as {section_name: [bullet, ...]}."""
    sections = {}
    current_section = None
    for line in body.splitlines():
        m_section = re.match(r"^\*\*(.+?)\*\*\s*$", line)
        if m_section:
            current_section = m_section.group(1)
            sections[current_section] = []
            continue
        if current_section and line.startswith("- "):
            sections[current_section].append(line.strip())
    return sections


def _extract_release_entry_titles(entries):
    """Extract bold release-note entry titles from bullet lines."""
    titles = []
    for entry in entries:
        m = re.match(r"-\s+\*\*(.+?)\*\*", entry)
        if m:
            titles.append(m.group(1).strip())
    return titles


def _check_changelog_entry_quality(strict=False):
    """检查 CHANGELOG 当前版本段落的条目质量。

    CHANGELOG 只包含用户可感知的变更。以下内容不应出现：
    - 新功能开发过程中的中间步骤（UI 微调等属于新功能本身）
    - 打包脚本、CI、发布流程优化（用户无感知）
    - 当前版本新功能引入的 bug 修复（不算「问题修复」）
    """
    version = _read_version()
    try:
        _title, body = _extract_changelog_release(version)
    except SystemExit:
        return  # _extract_changelog_release 已报错

    # 解析各分类下的条目（每个 "- **标题**：描述" 为一条）
    sections = {}
    current_section = None
    for line in body.splitlines():
        m_section = re.match(r"^###\s+(.+?)\s*$", line)
        if m_section:
            current_section = m_section.group(1)
            sections[current_section] = []
            continue
        if current_section and line.startswith("- "):
            sections[current_section].append(line.strip())

    warnings = []

    # 规则 1: 非用户感知关键词（出现即警告）
    # 每个元素为 (pattern, description)，pattern 用 \b 边界匹配避免子串误命中
    INTERNAL_PATTERNS = [
        (r"\bCI\b", "CI"),
        (r"\bworkflow\b", "workflow"),
        (r"\bpyinstaller\b", "PyInstaller"),
        (r"\bhook\b", "hook"),
        (r"\bvenv\b", "venv"),
        (r"\bsetup\.py\b", "setup.py"),
        (r"\bMANIFEST\b", "MANIFEST"),
        (r"\bgit\s+(push|tag|commit|rebase|reset)\b", "git 操作"),
        ("build\\.py", "build.py"),
        ("发布流程", "发布流程"),
        ("打包脚本", "打包脚本"),
        ("打包体积", "打包体积"),
        ("一键发布", "一键发布"),
        ("虚拟环境", "虚拟环境"),
        ("构建指纹", "构建指纹"),
        ("requirements\\.txt", "requirements.txt"),
        ("依赖管理", "依赖管理"),
        ("过程中", "过程中"),
        ("中间步骤", "中间步骤"),
        ("开发过程", "开发过程"),
    ]
    # 代码级描述关键词：条目正文中出现说明写的是实现细节而非用户感知
    CODE_LEVEL_PATTERNS = [
        (r"\bsys\.exit\b", "sys.exit"),
        (r"\bprint\(\s*[f\"']", "print() 调用"),
        (r"\blogger\.\w+\(", "logger 调用"),
        (r"\blogging\.\w+\(", "logging 调用"),
        (r"\btry\s*:\s*$", "try/except 描述"),
        (r"\bexcept\s+\w+", "except 描述"),
        (r"\bimport\s+\w+", "import 语句"),
        (r"\bfrom\s+\w+\s+import\b", "from import 语句"),
    ]
    for section_name, entries in sections.items():
        for entry in entries:
            # 规则 1a: 内部变更关键词（仅匹配条目正文，跳过标题）
            title_match = re.match(r"-\s+\*\*(.+?)\*\*[：:]?(.*)", entry)
            title_text = title_match.group(1) if title_match else ""
            body_text = title_match.group(2) if title_match else entry
            for pat, desc in INTERNAL_PATTERNS:
                if re.search(pat, body_text, re.IGNORECASE):
                    warnings.append(
                        f"  [{section_name}] {title_text} — 含内部变更关键词「{desc}」"
                    )
                    break

            # 规则 1b: 代码级描述（条目正文中出现代码语句）
            if title_text and body_text:
                for pat, desc in CODE_LEVEL_PATTERNS:
                    if re.search(pat, body_text, re.MULTILINE):
                        warnings.append(
                            f"  [{section_name}] {title_text} — 含代码级描述「{desc}」，"
                            f"请改为用户可感知的行为描述"
                        )
                        break

    # 规则 2+3: 体验优化/问题修复 与 新增功能 关键词重叠
    new_features = sections.get("新增功能", [])
    # 从新增功能条目中提取核心关键词（**标题** 中的中文词）
    feature_keywords = set()
    for entry in new_features:
        title_match = re.match(r"-\s+\*\*(.+?)\*\*", entry)
        if title_match:
            title_text = title_match.group(1)
            # 提取 2 字以上的中文词组
            cn_words = re.findall(r"[一-鿿]{2,}", title_text)
            feature_keywords.update(cn_words)

    if feature_keywords:
        for section_name in ("体验优化", "问题修复"):
            for entry in sections.get(section_name, []):
                title_match = re.match(r"-\s+\*\*(.+?)\*\*", entry)
                if not title_match:
                    continue
                title_text = title_match.group(1)
                # 检查该条目是否包含新增功能的关键词
                overlap = [kw for kw in feature_keywords if kw in title_text and len(kw) >= 3]
                if overlap:
                    warnings.append(
                        f"  [{section_name}] {title_text} — 与新增功能「{'」「'.join(overlap)}」重叠，"
                        f"可能属于新功能开发附属变更"
                    )

    # 规则 4: readme-style.md 写作规范（技术黑话 / 字段名 / 内部机制）
    # 这些词在用户可见的 CHANGELOG 中不应出现（详见 CLAUDE.md「版本内容写作规范」）
    STYLE_KEYWORDS = [
        # 内部机制关键词
        "listener", "API 兜底", "兜底预算", "闸门解耦", "持久化字段",
        "结构化数据", "去重合并", "时间戳组", "阶段 1.", "阶段 2.",
        "跨会话", "跨岗位", "风控面", "反馈环路",
        # 技术黑话
        "XML 1.0", "非法字符", "控制字符", "单元格", "校验失败",
        "正则", "子串匹配", "单词边界", "OR 条件", "AND 条件",
        "provider", "keyring", "DPI 缩放", "sha256", "SHA256",
        "locale-data", "openpyxl", "srcdoc", "iframe",
        # 与产品功能无关的版本工程过程
        "版本准备", "版本交付", "发布流程", "发布编排", "构建发布",
        "双远端", "发布门禁", "CI/CD", "自动更新清单", "版本标签",
        "打包脚本", "测试覆盖", "内部重构",
        # 内部文件名（用户不应接触）
        "job_config.json", "api_config.json", "selectors.json",
        "ui_config.json", "latest.json",
        # 内部函数名（v2.12 相关，未来按需补充）
        "parse_experience_years", "filter_candidate", "_extract_",
        "_detect_", "_parse_", "_build_", "greet_context",
    ]
    # 反引号包裹的内容通常是字段名/变量名/文件路径，用户不该看到
    BACKTICK_RE = re.compile(r"`[^`\s]+`")
    for section_name, entries in sections.items():
        for entry in entries:
            m = re.match(r"-\s+\*\*(.+?)\*\*[：:]?(.*)", entry)
            title_text = m.group(1) if m else ""
            body_text = m.group(2) if m else entry
            full_text = entry
            for kw in STYLE_KEYWORDS:
                if kw in full_text:
                    warnings.append(
                        f"  [{section_name}] {title_text} — 含技术黑话「{kw}」，"
                        f"按 readme-style.md 换简洁专业的表述"
                    )
                    break
            else:
                backticks = BACKTICK_RE.findall(full_text)
                if backticks:
                    warnings.append(
                        f"  [{section_name}] {title_text} — 含反引号标识 "
                        f"{'、'.join(backticks[:3])}（疑似字段名/变量名），"
                        f"请删除或换准确说法"
                    )

    if warnings:
        level = "错误" if strict else "警告"
        print(f"[{level}] CHANGELOG 中发现可能不属于用户感知变更的条目：\n")
        for w in warnings:
            print(w)
        print("\nCHANGELOG 应只包含用户可感知的变更。以下内容建议移除：")
        print("  - 新功能开发过程中的中间 UI 调整（属于新功能本身）")
        print("  - 打包脚本、CI、发布流程优化（用户无感知）")
        print("  - 当前版本新功能引入的 bug（不算「问题修复」）")
        print("同时应逐项核对上一公开 tag 到发布提交的用户变化，避免遗漏。")
        if strict:
            sys.exit(1)
        print("  [继续] 默认模式下仅提示；使用 --strict-changelog 可将此项作为硬门禁")
        return

    print("  [OK] CHANGELOG 条目质量检查通过")


def _check_changelog_code_coverage():
    """检查 CHANGELOG 当前版本各条目是否有对应的代码变更。

    对每个条目提取核心关键词，在 git diff 中搜索。找不到对应代码变更的条目
    可能属于"写了但没改"的幽灵条目，发出警告（非致命，因为可能涉及非 Python 文件变更）。
    """
    version = _read_version()
    try:
        _title, body = _extract_changelog_release(version)
    except SystemExit:
        return

    last_tag = _get_last_tag()
    if not last_tag:
        print("  [跳过] 代码覆盖检查：无法获取上一个 tag")
        return

    # 获取用户可见相关文件 diff（包含当前未提交工作区）
    diff_result = subprocess.run(
        ["git", "diff", last_tag, "--",
         "*.py", "*.json",
         ":(exclude)tests/*", ":(exclude)build.py",
         ":(exclude)scripts/*", ":(exclude)pyinstaller-hooks/*",
         ":(exclude)latest.json"],
        capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=BASE_DIR
    )
    if diff_result.returncode != 0:
        print("  [跳过] 代码覆盖检查：git diff 失败")
        return
    diff_text = diff_result.stdout.lower()
    if not diff_text.strip():
        print("  [跳过] 代码覆盖检查：无代码变更")
        return

    # 额外获取非 Python 文件变更列表（用于二次确认）
    other_result = subprocess.run(
        ["git", "diff", "--name-only", last_tag, "--", ".", ":(exclude)*.py"],
        capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=BASE_DIR
    )
    other_files = set(
        f.strip() for f in other_result.stdout.strip().splitlines()
        if f.strip() and not f.strip().endswith('.md')
    )

    # 解析条目
    entries = []
    current_section = None
    for line in body.splitlines():
        m = re.match(r"^###\s+(.+?)\s*$", line)
        if m:
            current_section = m.group(1)
            continue
        if current_section and line.startswith("- **"):
            entries.append((current_section, line.strip()))

    if not entries:
        return

    # 对每个条目提取关键词并在 diff 中搜索
    orphans = []
    for section, entry in entries:
        title_match = re.match(r"-\s+\*\*(.+?)\*\*[：:]?(.*)", entry)
        if not title_match:
            continue
        title = title_match.group(1)
        entry_body = title_match.group(2)
        # 提取标题 + 正文关键词；正文常包含 API/DOM/403 等更接近代码的证据。
        keywords = re.findall(r"[一-鿿]{2,}", f"{title} {entry_body}")
        keywords += [
            word.lower()
            for word in re.findall(r"\b[A-Za-z][A-Za-z0-9_]{2,}\b", entry_body)
            if word.lower() not in {"release", "notes"}
        ]
        if not keywords:
            continue
        found = any(kw.lower() in diff_text for kw in keywords)
        if not found:
            orphans.append((section, title))

    if orphans:
        print("[提示] 以下 CHANGELOG 条目未被正向关键词检查直接命中：")
        for section, title in orphans:
            print(f"  [{section}] {title}")
        if other_files:
            print(f"\n当前版本还修改了以下非 Python 文件：{', '.join(sorted(other_files))}")
        print("这一步只做幽灵条目提示；遗漏门禁由反向覆盖检查执行。\n")

    covered = len(entries) - len(orphans)
    print(f"  [OK] CHANGELOG 代码覆盖检查完成（{covered}/{len(entries)} 条目有对应 Python 代码变更）")


def _check_code_to_changelog_coverage(strict=False):
    """反向检查：git diff 中的用户可见变更是否被 CHANGELOG 覆盖。

    扫描 diff 中的强信号（UI 文本、新配置、行为变更、新数据、删除/限制），
    与 CHANGELOG 当前版本段落交叉比对。未覆盖的强信号会阻断发布。
    """
    version = _read_version()
    try:
        _title, body = _extract_changelog_release(version)
    except SystemExit:
        return

    last_tag = _get_last_tag()
    if not last_tag:
        print("  [跳过] 反向覆盖检查：无法获取上一个 tag")
        return

    # 获取 diff（包含当前未提交工作区；排除测试/构建/脚本/发布清单）
    diff_result = subprocess.run(
        ["git", "diff", last_tag, "--",
         "*.py", "*.json",
         ":(exclude)tests/*", ":(exclude)build.py",
         ":(exclude)scripts/*", ":(exclude)pyinstaller-hooks/*",
         ":(exclude)latest.json"],
        capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=BASE_DIR,
    )
    if diff_result.returncode != 0 or not diff_result.stdout.strip():
        print("  [跳过] 反向覆盖检查：无代码变更")
        return

    # 按文件解析 diff，保留 + / - 行。删除和限制类变更经常只出现在 - 行。
    file_additions: dict[str, list[str]] = {}
    file_removals: dict[str, list[str]] = {}
    current_file = None
    for line in diff_result.stdout.splitlines():
        if line.startswith("diff --git"):
            m = re.search(r"b/(\S+)$", line)
            current_file = m.group(1) if m else None
        elif current_file and line.startswith("+") and not line.startswith("+++"):
            file_additions.setdefault(current_file, []).append(line[1:])
        elif current_file and line.startswith("-") and not line.startswith("---"):
            file_removals.setdefault(current_file, []).append(line[1:])

    def _keywords(text: str) -> list[str]:
        """提取中英文关键词，用于与 CHANGELOG 做宽松交叉比对。"""
        # 常见 Python/Tk 标识符，不纳入关键词比对（误报过滤）
        _py_skip = {
            # Python 内建 / 常见方法 / 关键字
            "max", "min", "int", "str", "len", "set", "get", "new", "val", "var",
            "cls", "self", "super", "raw", "try", "err", "exc", "msg", "key",
            "tmp", "obj", "idx", "cnt", "num", "old", "cur", "buf", "src", "dst",
            "fmt", "ptr", "ref", "box", "row", "col", "pos", "off", "end", "beg",
            "and", "def", "elif", "else", "except", "args", "kwargs", "func", "meth",
            "getattr", "hasattr", "datetime", "strftime",
            # Tk / UI 框架方法和属性
            "ttk", "tk", "tframe", "tlabel", "tbutton",
            "pack", "forget", "grid", "place", "bind", "unbind",
            "after", "config", "style", "frame", "label", "widget", "canvas",
            "spinbox", "checkbutton", "radiobutton", "entry", "text", "button",
            "pady", "padx", "dpi", "zoom", "factor",
            # 回调 / 事件参数
            "callback", "event", "handler", "listener", "observer", "progress",
            "stop", "start", "begin", "finish", "complete", "abort", "cancel",
            # 日志 / UI 输出
            "log", "print", "append", "write", "read", "show", "hide", "display",
            "warn", "error", "debug", "info", "trace",
            # 通用英文修饰词
            "default", "enable", "disable", "enabled", "disabled", "active",
            "inactive", "valid", "invalid", "open", "close", "closed",
            "empty", "full", "auto", "manual", "safe", "unsafe",
            "effective", "actual", "real", "fake", "true", "false",
            "current", "previous", "next", "last", "first",
            "single", "multi", "double", "triple",
            "local", "global", "public", "private", "protected",
            "internal", "external", "main", "root", "base",
            # 常见动词 / 名词
            "create", "destroy", "build", "make", "init", "setup", "reset",
            "update", "delete", "remove", "clear", "save", "load", "store",
            "fetch", "refresh", "send", "recv", "call", "invoke", "trigger", "fire",
            "handle", "process", "parse", "format", "check", "test", "verify",
            "validate", "compare", "match", "find", "search", "select", "pick",
            "control", "controls", "configure", "command", "option", "setting",
            "manager", "window", "dialog", "panel", "view", "screen", "page",
            "menu", "item", "list", "array", "table", "chart", "graph", "map",
            "click", "press", "release", "focus", "blur", "enter", "leave",
            "drag", "drop", "move", "copy", "paste", "undo", "redo",
            "scroll", "scrollbar", "slider", "scale", "size", "width", "height",
            "font", "color", "background", "foreground", "border", "margin",
            "padding", "spacing", "align", "anchor", "side", "fill", "expand",
            "wrap", "truncate", "clip", "crop", "resize", "stretch",
            "stable", "pending", "ready", "done", "fail", "success",
            "type", "name", "title", "desc", "body", "head", "tail", "prefix",
            "suffix", "tag", "attr", "prop", "field", "token",
            "step", "count", "total", "limit", "rate", "freq", "level",
            "mode", "state", "status", "phase", "stage", "flag", "mask",
            "score", "updated", "updated_at", "resolution", "confirmed",
            "before", "after", "between", "during", "while",
            "job", "task", "work", "param", "value", "data", "info",
            "scan", "extract", "extraction", "diagnostics",
            "thread", "lock", "mutex", "semaphore", "queue", "stack", "heap",
            "cand", "column", "column_name",
            "file", "dir", "path", "line", "char", "byte", "word", "block",
            "batch", "loop", "iter", "recur", "chain", "pipe", "stream",
            "push", "pull", "pop", "peek", "flush", "drain", "buffer",
            "captcha", "requests", "exceptions", "timeout",
            "encode", "decode", "compress", "decompress", "hash", "sign",
            "encrypt", "decrypt", "auth", "token", "session", "cookie",
        }
        result: list[str] = []
        for run in re.findall(r"[一-鿿]+", text):
            if len(run) < 2:
                continue
            result.append(run)  # 保留完整段（用于长串匹配）
            for i in range(len(run) - 1):
                result.append(run[i:i + 2])  # 2 字窗口
        for word in re.findall(r"[A-Za-z][A-Za-z0-9_]{2,}", text):
            lowered = word.lower()
            parts = [p for p in word.split("_") if p]
            all_parts_skipped = bool(parts) and all(
                p.lower() in _py_skip for p in parts
            )
            if lowered in _py_skip or all_parts_skipped:
                continue
            result.append(lowered)
            result.extend(
                part.lower() for part in parts
                if len(part) >= 3 and part.lower() not in _py_skip
            )
        return result

    def _snippet(line: str, limit: int = 96) -> str:
        line = line.strip()
        return line if len(line) <= limit else line[:limit - 3] + "..."

    def _is_comment_or_doc_line(line: str) -> bool:
        stripped = line.strip()
        return (
            not stripped
            or stripped.startswith("#")
            or stripped.startswith('"""')
            or stripped.startswith("'''")
            or stripped in ('"""', "'''")
        )

    def _is_release_note_implementation_detail(fpath: str, line: str) -> bool:
        """Return True for implementation churn that should not force user-facing notes."""
        lowered = f"{fpath} {line}".lower()
        stripped = line.strip()
        if not re.search(r"[一-鿿]", stripped):
            if re.match(
                r"^[A-Za-z_]\w*(?:\.\w+)*(?:\s*:\s*[^=]+)?\s*=",
                stripped,
            ):
                return True
            if re.match(r"^(?:if|elif|for|while|and|or)\b", stripped):
                return True
            if re.match(r"^[A-Za-z_]\w*(?:\.\w+)+\(.*\)\s*$", stripped):
                return True
            if re.match(r"""^["'][A-Za-z_][A-Za-z0-9_ -]*["'],?$""", stripped):
                return True
        if re.search(r"\b\w*cache\w*\s*=", lowered):
            return True
        if re.search(r"getattr\(.*cache|source_cache|preview_render", lowered):
            return True
        if "pingfang sc" in lowered or "microsoft yahei" in lowered:
            return True
        if re.search(r"\b(font_family|font_family_semibold)\b", lowered):
            return True
        if fpath.endswith("icons.py") and re.search(
            r"#[^\n]*(品牌蓝|浅蓝|深蓝|高光)|#[^\n]*(brand|lens|highlight)",
            line,
            re.IGNORECASE,
        ):
            return True
        return False

    def _context_keywords(fpath: str, line: str) -> list[str]:
        lowered = f"{fpath} {line}".lower()
        keywords: list[str] = []
        if fpath.endswith("api_config.json"):
            # 发布模板中的服务商、模型库和探测缓存都属于“模型配置”语义。
            # 避免仅凭 providers/fetched_models 等内部键名制造无法落到用户文案的误报。
            keywords.extend(["模型", "配置"])
        if fpath.endswith("ui_config.json"):
            keywords.extend(["界面", "布局"])
        if fpath.endswith("constants.py") and "recommend" in lowered:
            keywords.extend(["候选人", "推荐"])
        if (
            "updater.py" in fpath
            or "gui_dialogs.py" in fpath
            or "release_notes" in lowered
            or "changelog" in lowered
        ):
            if re.search(r"release|changelog|notes|cache|cached|timeout|retry|gitee|github", lowered):
                keywords.extend(["更新日志", "远端", "release", "notes"])
        if "bossmaster.py" in fpath and re.search(r"captcha|verify|risk|403|412|429", lowered):
            keywords.extend(["风控", "验证码", "验证"])
        semantic_keywords = (
            (r"greet", ["打招呼"]),
            (r"candidate", ["候选人"]),
            (r"scan", ["扫描"]),
            (r"page|refresh", ["浏览器", "刷新"]),
            (r"date|time_range", ["时间"]),
            (r"browser|chromium|chrome", ["浏览器", "连接"]),
            (r"model|capability|protocol|provider|api_key", ["模型", "兼容"]),
            (r"qualification|education|edu_", ["学历", "资格"]),
            (r"\blog\b|log_", ["日志"]),
            (r"blacklist", ["黑名单"]),
            (r"resume", ["简历"]),
            (r"export|excel", ["导出"]),
            (r"followup", ["跟进"]),
            (r"feedback", ["反馈"]),
            (r"\bstats?\b|statistics", ["统计"]),
            (r"detail", ["明细"]),
        )
        for pattern, words in semantic_keywords:
            if re.search(pattern, lowered):
                keywords.extend(words)
        return keywords

    # 每条规则产出 (信号类型, 摘要, 关键词, 建议分类)
    signals: list[tuple[str, str, list[str], str]] = []
    behavior_re = re.compile(
        r"(风控|验证码|验证|弹窗|重试|超时|缓存|刷新|兜底|跳过|停止|暂停|"
        r"上限|限制|批次|间隔|延迟|扫描|打招呼|更新日志|远端|"
        r"\bAPI\b|\bDOM\b|listener|refresh|fallback|captcha|verify|risk|"
        r"retry|timeout|cache|ttl|limit|throttle|batch|403|412|429)",
        re.IGNORECASE,
    )
    data_re = re.compile(
        r"(candidate|candidates|feedback|followup|blacklist|resume|score|"
        r"export|excel|status|reason|note|updated_at|geek_id|job_name)",
        re.IGNORECASE,
    )
    ui_files = ("gui_main.py", "gui_dialogs.py", "ui_messagebox.py", "updater.py")

    for fpath in sorted(set(file_additions) | set(file_removals)):
        additions = file_additions.get(fpath, [])
        removals = file_removals.get(fpath, [])
        normalized_removals = {line.strip() for line in removals if line.strip()}
        signal_additions = [
            line for line in additions
            if line.strip() not in normalized_removals
        ]
        joined = "\n".join(signal_additions)

        # 1. 新配置 / 阈值 / 默认值
        if "constants.py" in fpath:
            for m in re.finditer(
                r"^\+?([A-Z][A-Z0-9_]{3,})\s*=\s*(.+)", joined, re.MULTILINE
            ):
                name, value = m.group(1), m.group(2).strip()[:60]
                signals.append((
                    "新配置", f"{fpath}: {name} = {value}",
                    _keywords(f"{name} {value}") + _context_keywords(fpath, name),
                    "体验优化",
                ))

        if fpath.endswith((".json", ".JSON")):
            for m in re.finditer(r'"(\w+)"\s*:\s*\{', joined):
                group = m.group(1)
                if group not in ("version",):
                    signals.append((
                        "新配置", f"{fpath}: {group}",
                        _keywords(group) + _context_keywords(fpath, group),
                        "新增功能" if fpath.endswith("selectors.json") else "体验优化",
                    ))

        # 2. 新错误处理路径
        if any(k in fpath for k in ("bossmaster.py", "gui_main.py")):
            for m in re.finditer(
                r"class\s+(\w+)\s*\(\s*\w*Exception\w*\s*\)", joined
            ):
                signals.append((
                    "新异常类", m.group(1),
                    _keywords(joined[max(0, m.start()-100):m.end()+100]),
                    "体验优化",
                ))

        # 3. 核心文件新增公开函数（不以 _ 开头 = 用户可见的新入口）
        if fpath in ("bossmaster.py", "gui_main.py"):
            for m in re.finditer(r"^def\s+([a-z]\w{5,})\s*\(", joined, re.MULTILINE):
                fname = m.group(1)
                after = joined[m.end():m.end()+300]
                signals.append((
                    "新函数",
                    f"{fpath}: {fname}",
                    _keywords(f"{fname} {after}") + _context_keywords(fpath, fname),
                    "新增功能",
                ))

        # 4. UI 文案 / 弹窗 / 用户可见输出
        if fpath.endswith(".py"):
            for line in signal_additions:
                if _is_comment_or_doc_line(line):
                    continue
                if not re.search(r"[一-鿿]{3,}", line):
                    continue
                if "print(" in line or any(ui_file in fpath for ui_file in ui_files):
                    text = _snippet(re.sub(r"\s+", " ", line))
                    signals.append((
                        "UI文本", f"{fpath}: {text}",
                        _keywords(text) + _context_keywords(fpath, line),
                        "体验优化",
                    ))

        # 5. 行为变更：风控、兜底、重试、缓存、上限、节奏等
        for line in signal_additions:
            if _is_comment_or_doc_line(line):
                continue
            if _is_release_note_implementation_detail(fpath, line):
                continue
            if not re.search(r"[一-鿿]{2,}", line) and (
                re.search(r"^\s*def\s+_\w+\s*\(", line)
                or re.search(r"^\s*def\s+\w+\s*\(", line)
                or re.search(r"^\s*['\"][A-Za-z_][A-Za-z0-9_]{2,}['\"]\s*:", line)
                or re.search(r"^\s*except\s+[\w.]+:", line)
            ):
                continue
            if behavior_re.search(line):
                text = _snippet(re.sub(r"\s+", " ", line))
                context_words = _context_keywords(fpath, line)
                if not re.search(r"[一-鿿]{2,}", line) and not context_words:
                    continue
                signals.append((
                    "行为变更", f"{fpath}: {text}",
                    _keywords(text) + context_words,
                    "体验优化",
                ))

        # 6. 新数据字段：持久化、导出、状态、评分、候选人结构
        if fpath.endswith(".py") and any(k in fpath for k in ("storage.py", "filtering.py", "bossmaster.py", "gui_main.py", "llm_eval.py")):
            for line in signal_additions:
                if _is_comment_or_doc_line(line):
                    continue
                if not re.search(r"[一-鿿]{2,}", line) and re.search(
                    r"^\s*['\"][A-Za-z_][A-Za-z0-9_]{2,}['\"]\s*:", line
                ):
                    continue
                field_match = re.search(
                    r"(?:^|[{,])\s*['\"]([A-Za-z_][A-Za-z0-9_]{2,})['\"]\s*:",
                    line,
                )
                if (
                    field_match
                    and field_match.group(1) in {
                        "status", "error", "msg", "message", "time",
                        "response_time", "capability", "reason", "adjustment",
                        "name", "skip",
                    }
                    and not re.search(r"candidate|qualification|llm_|followup|feedback|blacklist|resume", line)
                ):
                    continue
                if data_re.search(line) and field_match:
                    text = _snippet(re.sub(r"\s+", " ", line))
                    signals.append((
                        "新数据",
                        f"{fpath}: {text}",
                        _keywords(text) + _context_keywords(fpath, line),
                        "新增功能",
                    ))

        # 7. 删除 / 限制：删除 UI 文案、函数入口、配置键，或新增限制逻辑
        normalized_additions = {line.strip() for line in additions if line.strip()}
        added_function_names = {
            match.group(1)
            for line in additions
            if (match := re.search(r"\bdef\s+([a-zA-Z_]\w*)\s*\(", line))
        }
        for line in removals:
            if _is_comment_or_doc_line(line):
                continue
            if _is_release_note_implementation_detail(fpath, line):
                continue
            if re.search(r'\\"[A-Za-z_][A-Za-z0-9_]+\\"\s*:', line):
                # Removed JSON examples inside prompts are implementation details.
                continue
            if line.strip() in normalized_additions:
                continue
            any_removed_func = re.search(r"\bdef\s+([a-zA-Z_]\w*)\s*\(", line)
            if any_removed_func:
                # Only top-level public functions are release-note signals.
                # Nested helpers are implementation details even when their
                # local names do not start with an underscore.
                removed_func = re.search(r"^def\s+([a-zA-Z_]\w*)\s*\(", line)
                if removed_func is None:
                    continue
                if removed_func.group(1) in added_function_names:
                    continue
                if removed_func.group(1).startswith("_"):
                    continue
            if (
                re.search(r"[一-鿿]{3,}", line)
                or re.search(r"def\s+[a-zA-Z_]\w+\s*\(", line)
                or (
                    fpath.endswith((".json", ".JSON"))
                    and re.search(r'"[A-Za-z_][A-Za-z0-9_]+"\s*:', line)
                )
            ):
                text = _snippet(re.sub(r"\s+", " ", line))
                signals.append((
                    "删除/限制",
                    f"{fpath}: removed {text}",
                    _keywords(text) + _context_keywords(fpath, line),
                    "体验优化",
                ))
        for line in signal_additions:
            if _is_comment_or_doc_line(line):
                continue
            if _is_release_note_implementation_detail(fpath, line):
                continue
            if re.search(r"(limit|max|上限|限制|停止|跳过|return\s+|raise\s+)", line, re.IGNORECASE) and behavior_re.search(line):
                text = _snippet(re.sub(r"\s+", " ", line))
                context_words = _context_keywords(fpath, line)
                if not re.search(r"[一-鿿]{2,}", line) and not context_words:
                    continue
                signals.append((
                    "删除/限制", f"{fpath}: {text}",
                    _keywords(text) + context_words,
                    "体验优化",
                ))

    if not signals:
        print("  [OK] 反向覆盖检查：未检测到用户可见的新增代码信号")
        return

    # 去重，避免同一行同时命中多条规则造成噪声过大。
    deduped = []
    seen = set()
    for signal in signals:
        key = (signal[0], signal[1])
        if key in seen:
            continue
        seen.add(key)
        deduped.append(signal)
    signals = deduped

    changelog_text = _normalize_markdown(body).lower()
    sections = _parse_changelog_sections(body)
    uncovered = []
    for category, summary, keywords, required_section in signals:
        useful_keywords = [kw for kw in keywords if len(kw) >= 2]
        if not useful_keywords:
            # 所有关键词都被过滤（纯 Python/Tk 内建标识符），无需 changelog 条目
            continue
        if not any(kw.lower() in changelog_text for kw in useful_keywords):
            uncovered.append((category, summary, required_section))
    missing_sections = sorted(
        {required for *_rest, required in uncovered if required not in sections},
        key=lambda name: CHANGELOG_REQUIRED_SECTIONS.index(name)
        if name in CHANGELOG_REQUIRED_SECTIONS else 99,
    )

    if missing_sections or uncovered:
        level = "错误" if strict else "警告"
        print(f"[{level}] git diff 中检测到疑似用户可见变更未被 CHANGELOG v{version} 完整覆盖：")
        if missing_sections:
            print("\n缺少建议分类：")
            for section in missing_sections:
                print(f"  - {section}")
        if uncovered:
            print("\n未覆盖信号：")
        for category, summary, required_section in uncovered[:12]:
            print(f"  [{category} -> 建议 {required_section}] {summary}")
        if len(uncovered) > 12:
            print(f"  ... 另有 {len(uncovered) - 12} 个（仅展示前 12 个）")
        print("\n请复核 CHANGELOG；这一步基于关键词启发式，可能存在误报。")
        if strict:
            print("严格模式已开启，请补充 CHANGELOG，或调整检查规则避免误报。")
            sys.exit(1)
        print("  [继续] 默认模式下仅提示；使用 --strict-changelog 可将此项作为硬门禁")
    else:
        print(
            f"  [OK] 反向覆盖检查：{len(signals)} 个代码信号"
            f"均已在 CHANGELOG 中体现"
        )


def _check_todo_not_stale():
    """检测 TODO.md 是否还保留已完成的发布事项。"""
    todo_path = BASE_DIR / "TODO.md"
    if not todo_path.exists():
        return

    content = todo_path.read_text(encoding="utf-8")
    stale_items = [
        ("国内备用更新源", "Gitee/GitHub 双源更新已在 v2.8.8 落地"),
    ]
    found = [
        (keyword, reason)
        for keyword, reason in stale_items
        if re.search(rf"^\s*-\s+\[\s*\]\s+.*{re.escape(keyword)}", content, re.MULTILINE)
    ]
    if found:
        print("[错误] TODO.md 含已完成但仍未勾选的事项：")
        for keyword, reason in found:
            print(f"  - {keyword}：{reason}")
        print("请先删除、勾选或改写为真实未完成事项。")
        sys.exit(1)

    print("  [OK] TODO.md 无已完成待办残留")


def _check_claude_md_size():
    """检查 CLAUDE.md 行数不超过 300 行（防止知识文档膨胀）。"""
    claude_path = BASE_DIR / "CLAUDE.md"
    if not claude_path.exists():
        return

    line_count = sum(1 for _ in claude_path.open(encoding="utf-8"))
    limit = 300
    if line_count > limit:
        print(f"[错误] CLAUDE.md 当前 {line_count} 行，超过 {limit} 行限制")
        print("  请先执行 /neat-freak 精简后再发布。")
        sys.exit(1)

    print(f"  [OK] CLAUDE.md {line_count}/{limit} 行")


def _check_project_docs_version_sync(version):
    """检查 CLAUDE.md 和 AGENTS.md 项目结构中的 gui_main.py 版本注释。"""
    pattern = re.compile(
        rf"gui_main\.py\s+#\s+图形界面主程序（v{re.escape(version)}）"
    )
    for doc_name in ("CLAUDE.md", "AGENTS.md"):
        doc_path = BASE_DIR / doc_name
        if not doc_path.exists():
            continue
        content = doc_path.read_text(encoding="utf-8")
        if not pattern.search(content):
            print(f"[错误] {doc_name} 项目结构中的 gui_main.py 版本未同步为 v{version}")
            print(f"请将 {doc_name} 项目结构中的 gui_main.py 标注更新为："
                  f"gui_main.py{' ' * 12}# 图形界面主程序（v{version}）")
            sys.exit(1)
    print(f"  [OK] CLAUDE.md / AGENTS.md 项目结构版本注释已同步 v{version}")


def _preflight_checks(
    require_clean=True,
    strict_changelog=False,
    *,
    run_tests=True,
):
    """发布/打包前检查"""
    print("\n>>> 发布前检查")
    if require_clean:
        _run_preflight_step("工作区干净检查（前置）", _check_worktree_clean)
    _run_preflight_step("依赖检查", _check_dependencies)
    _run_preflight_step("Tcl/Tk 运行库检查", _check_tkinter_packaging_support)
    _run_preflight_step("存储文件跟踪检查", _check_storage_not_tracked)
    _run_preflight_step("敏感/运行数据跟踪检查", _check_sensitive_files_not_tracked)
    _run_preflight_step("API 配置明文 Key 检查", _check_api_config_has_no_plaintext_key)
    _run_preflight_step("源码编译检查", _check_source_compiles)
    _run_preflight_step("Ruff F821 未定义名称检查", _check_undefined_names)
    _run_preflight_step("CHANGELOG 版本同步检查", _check_changelog_updated)
    _run_preflight_step(
        "CHANGELOG 条目质量检查",
        _check_changelog_entry_quality,
        strict=strict_changelog,
    )
    _run_preflight_step("CHANGELOG 正向覆盖检查", _check_changelog_code_coverage)
    _run_preflight_step(
        "CHANGELOG 反向覆盖检查",
        _check_code_to_changelog_coverage,
        strict=strict_changelog,
    )
    _run_preflight_step("TODO 时效检查", _check_todo_not_stale)
    _run_preflight_step("CLAUDE.md 尺寸检查", _check_claude_md_size)
    if run_tests:
        _run_preflight_step("稳定单元回归与导入烟测", _run_unit_checks)
    else:
        print("  [复用] 产品代码指纹未变化，跳过重复单元回归与导入烟测")
    current_version = _read_version()
    _run_preflight_step("Release 说明提取检查", _extract_changelog_release, current_version)
    _run_preflight_step(
        "README 发布说明检查",
        _check_readme_release,
        current_version,
        strict_details=strict_changelog,
    )
    _run_preflight_step(
        "latest.json 发布说明检查",
        _check_latest_json_release_notes,
        current_version,
        strict=strict_changelog,
    )
    _run_preflight_step(
        "CLAUDE.md / AGENTS.md 版本同步检查",
        _check_project_docs_version_sync,
        current_version,
    )
    _run_preflight_step("版本历史完整性检查", _check_version_history_integrity)

    if require_clean:
        _run_preflight_step("工作区干净检查（收尾）", _check_worktree_clean)

    print(">>> 发布前检查通过\n")


def _validate_prepared_ci_build(prepared_sha):
    """Allow build jobs to reuse the strict gate from the same workflow SHA."""
    if os.environ.get("GITHUB_ACTIONS") != "true":
        print("错误: --prepared-sha 只能在 GitHub Actions 中使用")
        sys.exit(1)
    if os.environ.get("GITHUB_EVENT_NAME") != "workflow_dispatch":
        print("错误: --prepared-sha 只能用于手动正式发布工作流")
        sys.exit(1)
    if not re.fullmatch(r"[0-9a-fA-F]{40}", prepared_sha or ""):
        print("错误: --prepared-sha 必须是完整的 Git 提交 SHA")
        sys.exit(1)
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=BASE_DIR,
        capture_output=True, text=True, check=True,
    )
    if result.stdout.strip().lower() != prepared_sha.lower():
        print("错误: 打包任务检出的提交与严格门禁通过的提交不一致")
        sys.exit(1)
    _check_source_compiles()
    _check_undefined_names()
    print(f"  [OK] 复用同一工作流的严格门禁: {prepared_sha[:12]}")


def _read_version():
    """AST 解析 gui_main.py 提取 __version__"""
    gui_path = BASE_DIR / "gui_main.py"
    with open(gui_path, encoding="utf-8") as f:
        tree = ast.parse(f.read())
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
            if isinstance(target, ast.Name) and target.id == "__version__":
                if isinstance(node.value, ast.Constant):
                    return node.value.value
    print("[错误] 无法从 gui_main.py 提取 __version__")
    sys.exit(1)


def _write_version(version: str):
    """将 __version__ = "X.Y" 写入 gui_main.py"""
    gui_path = BASE_DIR / "gui_main.py"
    content = gui_path.read_text(encoding="utf-8")
    new_content = re.sub(
        r'^__version__\s*=\s*"[^"]*"',
        f'__version__ = "{version}"',
        content,
        flags=re.MULTILINE
    )
    if new_content == content:
        print(f"[错误] 无法在 gui_main.py 中定位 __version__ 行")
        sys.exit(1)
    gui_path.write_text(new_content, encoding="utf-8")
    print(f"  [OK] __version__ = \"{version}\"\n")


def _validate_version_format(version: str):
    """验证版本号格式是否符合项目惯例。

    规范：
    - 大版本：X.Y（如 2.9）
    - 补丁版本：X.Y.Z（如 2.8.12）
    - 禁止：大版本写成 X.Y.0（如 2.9.0）
    """
    parts = version.split(".")

    # 检查是否为数字
    if not all(p.isdigit() for p in parts):
        print(f"[错误] 版本号格式错误：{version}")
        print(f"  版本号只能包含数字和点号，例如：2.9 或 2.8.12")
        sys.exit(1)

    # 检查段数
    if len(parts) not in (2, 3):
        print(f"[错误] 版本号格式错误：{version}")
        print(f"  版本号必须是 X.Y 或 X.Y.Z 格式，例如：2.9 或 2.8.12")
        sys.exit(1)

    # 检查大版本是否误写为 X.Y.0
    if len(parts) == 2 and parts[1] == "0":
        # X.0 是合法的（如 2.0）
        pass
    elif len(parts) == 3 and parts[2] == "0":
        print(f"[错误] 版本号格式错误：{version}")
        print(f"  大版本必须使用 X.Y 格式，禁止写成 X.Y.0")
        print(f"  请改为：{'.'.join(parts[:2])}")
        sys.exit(1)

    # 检查补丁版本是否合理（Z 不能为 0）
    if len(parts) == 3 and int(parts[2]) == 0:
        print(f"[错误] 版本号格式错误：{version}")
        print(f"  补丁版本的第三段不能为 0，请改为：{'.'.join(parts[:2])}")
        sys.exit(1)


def _is_valid_release_version(version: str) -> bool:
    """Return whether version follows the public release version rules."""
    parts = version.split(".")
    if len(parts) not in (2, 3) or not all(part.isdigit() for part in parts):
        return False
    if len(parts) == 3 and int(parts[2]) == 0:
        return False
    return True


def _is_valid_release_tag(tag: str) -> bool:
    """Return whether tag is a valid public release tag such as v2.9 or v2.9.1."""
    if not tag.startswith("v"):
        return False
    return _is_valid_release_version(tag[1:])


def _check_version_consistency():
    """读取 __version__ 并与打包产物核对，返回 (version, artifact_path, size_mb)"""
    version = _read_version()

    if IS_MAC:
        # macOS: 检查 .app、.zip、.dmg
        app_path = DIST_DIR / "BOSS_ResumeFilter.app"
        zip_path = DIST_DIR / "BOSS_ResumeFilter_mac.zip"
        dmg_path = DIST_DIR / "BOSS_ResumeFilter.dmg"

        for path, label in [(app_path, ".app"), (zip_path, "ZIP"), (dmg_path, "DMG")]:
            if not path.exists():
                print(f"[错误] {label} 不存在：{path}")
                sys.exit(1)

        size_mb = zip_path.stat().st_size / (1024 * 1024)
        dmg_size_mb = dmg_path.stat().st_size / (1024 * 1024)
        print(f"\n{'='*60}")
        print(f"  打包完成: v{version}")
        print(f"  APP:  {app_path}")
        print(f"  ZIP:  {zip_path} ({size_mb:.1f} MB)")
        print(f"  DMG:  {dmg_path} ({dmg_size_mb:.1f} MB)")
        print(f"{'='*60}")
        return version, zip_path, size_mb
    else:
        # Windows: 检查 .exe
        exe_path = DIST_DIR / "BOSS_ResumeFilter.exe"
        if not exe_path.exists():
            print(f"[错误] EXE 不存在：{exe_path}")
            sys.exit(1)

        size_mb = exe_path.stat().st_size / (1024 * 1024)
        print(f"\n{'='*60}")
        print(f"  打包完成: v{version}")
        print(f"  EXE:  {exe_path} ({size_mb:.1f} MB)")
        print(f"{'='*60}")
        return version, exe_path, size_mb


def _sha256_file(path):
    """Return SHA256 hex digest for a release artifact."""
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _asset_digest_sha256(asset_info):
    """Extract a sha256 digest from release asset metadata when available."""
    for key in ("digest", "sha256"):
        value = asset_info.get(key) if asset_info else None
        if not value:
            continue
        value = str(value)
        if value.startswith("sha256:"):
            value = value.split(":", 1)[1]
        if re.fullmatch(r"[0-9a-fA-F]{64}", value):
            return value.lower()
    return None


def _remote_file_sha256(url, token=None):
    """Download a remote file stream and return its SHA256 digest."""
    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    session = _gitee_session()
    resp = session.get(url, headers=headers, stream=True, timeout=TRANSFER_TIMEOUT_SECONDS)
    resp.raise_for_status()
    digest = hashlib.sha256()
    for chunk in resp.iter_content(chunk_size=1024 * 1024):
        if chunk:
            digest.update(chunk)
    return digest.hexdigest()


def _transfer_item_name(item):
    return item.name if isinstance(item, Path) else str(item)


def _transfer_item_size(item, remote_assets=None):
    if isinstance(item, Path) and item.exists():
        return item.stat().st_size
    if remote_assets:
        info = remote_assets.get(_transfer_item_name(item), {})
        try:
            return int(info.get("size") or 0)
        except (TypeError, ValueError):
            return 0
    return 0


def _is_large_transfer_item(item, remote_assets=None):
    size = _transfer_item_size(item, remote_assets)
    if size:
        return size >= LARGE_TRANSFER_THRESHOLD
    return Path(_transfer_item_name(item)).suffix.lower() in {".exe", ".dmg", ".zip"}


def _run_transfer_batch(items, label, worker, on_success, on_failure,
                        remote_assets=None, large_workers=1):
    """Run small transfers concurrently, then large transfers with bounded concurrency."""
    if not items:
        return

    large = [item for item in items if _is_large_transfer_item(item, remote_assets)]
    small = [item for item in items if item not in large]

    if small:
        workers = min(SMALL_TRANSFER_WORKERS, len(small))
        print(f"  {label} 小文件并发: {len(small)} 个 (workers={workers})")
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(worker, item): item for item in small}
            for future in as_completed(futures):
                item = futures[future]
                try:
                    on_success(item, future.result())
                except Exception as e:
                    on_failure(item, e)

    if large_workers > 1 and len(large) > 1:
        workers = min(large_workers, len(large))
        print(f"  {label} 大文件并发: {len(large)} 个 (workers={workers})")
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(worker, item): item for item in large}
            for future in as_completed(futures):
                item = futures[future]
                try:
                    on_success(item, future.result())
                except Exception as e:
                    on_failure(item, e)
    else:
        for item in large:
            name = _transfer_item_name(item)
            print(f"  {label} 大文件串行: {name}")
            try:
                on_success(item, worker(item))
            except Exception as e:
                on_failure(item, e)


def _github_asset_matches_local(tag, local_path, remote_asset):
    """Return True when a GitHub Release asset has identical file content."""
    local_size = local_path.stat().st_size
    remote_size = int(remote_asset.get("size") or 0)
    if remote_size != local_size:
        return False, f"大小不一致 (本地 {local_size} vs 远端 {remote_size})"

    local_sha = _sha256_file(local_path)
    remote_sha = _asset_digest_sha256(remote_asset)
    if remote_sha:
        if remote_sha == local_sha:
            return True, f"SHA256 一致 ({local_size} bytes)"
        return False, "SHA256 不一致"

    if _latest_json_asset_matches_local(local_path):
        return True, f"latest.json 元数据一致 ({local_size} bytes)"

    compare_dir = Path(tempfile.mkdtemp(prefix="gh_asset_compare_"))
    try:
        remote_path = _download_from_github_release(tag, local_path.name, compare_dir)
        remote_sha = _sha256_file(remote_path)
        if remote_sha == local_sha:
            return True, f"SHA256 一致 ({local_size} bytes)"
        return False, "SHA256 不一致"
    finally:
        shutil.rmtree(compare_dir, ignore_errors=True)


def _upload_github_release_asset(tag, path, report=None):
    """Upload one asset to GitHub Release with retry and timeout."""
    report = report or (lambda msg: print(f"  {msg}"))
    for attempt in range(4):
        try:
            subprocess.run(
                ["gh", "release", "upload", tag, str(path), "--clobber"],
                cwd=BASE_DIR,
                check=True,
                timeout=TRANSFER_TIMEOUT_SECONDS,
            )
            return path.name
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
            if attempt < 3:
                wait = 2 * (attempt + 1)
                report(f"[重试] {path.name} 上传失败 (attempt {attempt+1}/3), {wait}s 后重试...")
                time.sleep(wait)
            else:
                raise


def _ensure_github_release_asset_matches_local(tag, local_path, report=None,
                                               max_wait=90, poll_interval=5):
    """Ensure the GitHub Release asset for local_path matches the local file."""
    report = report or (lambda msg: print(f"  {msg}"))
    deadline = time.time() + max_wait
    uploaded = False

    while True:
        remote_assets = _get_github_release_assets(tag)
        remote_asset = remote_assets.get(local_path.name)
        if remote_asset:
            same, reason = _github_asset_matches_local(tag, local_path, remote_asset)
            if same:
                if uploaded:
                    report(f"[OK] {local_path.name} 远端校验通过: {reason}")
                return True
            report(f"[修正] {local_path.name} 远端不一致: {reason}")
        else:
            report(f"[修正] {local_path.name} 远端缺失")

        _upload_github_release_asset(tag, local_path, report=report)
        uploaded = True

        if time.time() >= deadline:
            break
        time.sleep(poll_interval)

    remote_assets = _get_github_release_assets(tag)
    remote_asset = remote_assets.get(local_path.name)
    if remote_asset:
        same, reason = _github_asset_matches_local(tag, local_path, remote_asset)
        if same:
            report(f"[OK] {local_path.name} 远端校验通过: {reason}")
            return True
        print(f"[错误] {local_path.name} 上传后仍与本地不一致: {reason}")
    else:
        print(f"[错误] {local_path.name} 上传后 GitHub Release 仍缺少该资产")
    return False


def _ensure_current_platform_github_assets_match_local(tag, artifact_paths, report=None):
    """Verify current-platform update assets before publishing manifest metadata."""
    required_names = _current_platform_update_artifact_names()
    for path in artifact_paths:
        if path.name not in required_names:
            continue
        if not _ensure_github_release_asset_matches_local(tag, path, report=report):
            sys.exit(1)


def _required_release_asset_names():
    """Assets that must exist on a public release before the release is complete."""
    return {
        "BOSS_ResumeFilter.exe",
        "BOSS_ResumeFilter_mac.zip",
        "BOSS_ResumeFilter.dmg",
    }


def _release_integrity_asset_names():
    """Binary update packages whose size and SHA256 must be cross-checked."""
    return {
        "BOSS_ResumeFilter.exe",
        "BOSS_ResumeFilter_mac.zip",
        "BOSS_ResumeFilter.dmg",
    }


def _verify_github_release_assets_complete(tag, report=None):
    """Verify GitHub Release has every required asset and binary digests."""
    report = report or (lambda msg: print(f"  {msg}"))
    assets = _get_github_release_assets(tag)
    required = _required_release_asset_names()
    missing = sorted(required - set(assets))
    if missing:
        report(f"[错误] GitHub Release 缺少附件: {', '.join(missing)}")
        return None

    for name in sorted(_release_integrity_asset_names()):
        asset = assets.get(name)
        try:
            size = int(asset.get("size") or 0)
        except (TypeError, ValueError):
            size = 0
        sha256 = _asset_digest_sha256(asset)
        if size <= 0 or not sha256:
            report(f"[错误] GitHub Release {name} 缺少 size/SHA256 元数据")
            return None

    report("[OK] GitHub Release 附件齐全，更新包 size/SHA256 可用")
    return assets


def _verify_gitee_release_assets_complete(tag, github_assets, release_cache,
                                           report=None, verify_sha256=False):
    """Verify Gitee mirrors GitHub; optionally download assets for SHA256 audit."""
    report = report or (lambda msg: print(f"  {msg}"))
    token = release_cache["token"]
    owner = release_cache["owner"]
    repo = release_cache["repo"]
    api_base = release_cache["api_base"]
    release_id = release_cache["release_id"]

    try:
        gitee_assets = _gitee_fetch_assets(api_base, token, release_id)
        release_cache["existing"] = gitee_assets
    except requests.exceptions.RequestException as e:
        detail = release_retry.redact_sensitive_text(e, (token,))
        report(f"[错误] Gitee Release 附件列表读取失败: {detail}")
        return False

    required = _required_release_asset_names()
    missing = sorted(required - set(gitee_assets))
    if missing:
        report(f"[错误] Gitee Release 缺少附件: {', '.join(missing)}")
        return False

    for name in sorted(_release_integrity_asset_names()):
        github_asset = github_assets[name]
        gitee_asset = gitee_assets[name]
        try:
            github_size = int(github_asset.get("size") or 0)
            gitee_size = int(gitee_asset.get("size") or 0)
        except (TypeError, ValueError):
            report(f"[错误] {name} size 元数据无法解析")
            return False
        if gitee_size != github_size:
            report(f"[错误] Gitee {name} 大小不一致 (GitHub {github_size} vs Gitee {gitee_size})")
            return False

        if verify_sha256:
            expected_sha = _asset_digest_sha256(github_asset)
            if not expected_sha:
                report(f"[错误] GitHub {name} 缺少 SHA256，无法校验 Gitee 镜像")
                return False
            try:
                gitee_sha = _remote_file_sha256(
                    _gitee_asset_url(owner, repo, tag, name),
                    token=token,
                )
            except requests.exceptions.RequestException as e:
                detail = release_retry.redact_sensitive_text(e, (token,))
                report(f"[错误] Gitee {name} SHA256 校验失败: {detail}")
                return False
            if gitee_sha != expected_sha:
                report(f"[错误] Gitee {name} SHA256 不一致")
                return False

    if verify_sha256:
        report("[OK] Gitee Release 附件齐全，size/SHA256 与 GitHub 一致")
    else:
        report("[OK] Gitee Release 附件齐全，size 与 GitHub 一致（未回下载大文件）")
    return True


def _verify_release_assets_complete(tag, release_cache=None, report=None,
                                    verify_gitee_sha256=False):
    """Verify final release asset completeness before declaring release success."""
    report = report or (lambda msg: print(f"  {msg}"))
    github_assets = _verify_github_release_assets_complete(tag, report=report)
    if github_assets is None:
        return False
    if not release_cache:
        report("[跳过] Gitee Release 完整性校验：未启用 Gitee 上传")
        return True
    return _verify_gitee_release_assets_complete(
        tag,
        github_assets,
        release_cache,
        report=report,
        verify_sha256=verify_gitee_sha256,
    )


def _remote_ref_commit(remote: str, ref: str) -> str | None:
    """Return one advertised remote ref, retrying three transient failures."""
    for attempt in range(4):
        result = subprocess.run(
            ["git", "ls-remote", remote, ref],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=BASE_DIR,
        )
        if result.returncode == 0:
            for line in result.stdout.splitlines():
                parts = line.split()
                if len(parts) == 2 and parts[1] == ref:
                    return parts[0]
            return None
        detail = str(result.stderr or result.stdout or "git ls-remote failed").lower()
        deterministic = any(
            marker in detail
            for marker in (
                "authentication failed",
                "permission denied",
                "repository not found",
            )
        )
        if deterministic or attempt >= 3:
            return None
        delay = 2 * (attempt + 1)
        print(
            f"  [重试] 读取 {remote}/{ref} 瞬时失败，准备第 "
            f"{attempt + 1}/3 次重试（{delay}s 后）"
        )
        time.sleep(delay)
    return None


def _github_release_view_json(
    tag: str,
    fields: str,
    *,
    attempts: int = 4,
    base_delay: int = 2,
) -> dict | None:
    """Query one GitHub Release with bounded retries for transient CLI failures."""
    last_error = ""
    for attempt in range(1, attempts + 1):
        result = subprocess.run(
            ["gh", "release", "view", tag, "--json", fields],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=BASE_DIR,
        )
        if result.returncode == 0:
            try:
                data = json.loads(result.stdout)
            except json.JSONDecodeError as exc:
                last_error = f"JSONDecodeError: {exc}"
            else:
                return data if isinstance(data, dict) else None
        else:
            last_error = (result.stderr or result.stdout or "gh query failed").strip()
            lowered = last_error.lower()
            if "release not found" in lowered or "http 404" in lowered:
                return None

        if attempt < attempts:
            delay = base_delay * attempt
            print(
                f"  [GitHub 重试] 查询 {tag} 失败 "
                f"({attempt}/{attempts})，{delay}s 后重试..."
            )
            time.sleep(delay)

    if last_error:
        print(f"  [GitHub 警告] 查询 {tag} 失败：{last_error}")
    return None


def _get_github_release_info(tag: str) -> dict | None:
    """Read public GitHub Release metadata without changing the release."""
    return _github_release_view_json(
        tag,
        "tagName,name,body,isDraft,isPrerelease",
    )


def _get_gitee_release_read_only(version: str) -> tuple[dict, dict] | None:
    """Read one Gitee Release and its assets without creating or editing it."""
    owner = "yaoyouzhong"
    repo = "boss-resume-filter"
    tag = f"v{version}"
    token = os.environ.get("GITEE_TOKEN")
    api_base = f"https://gitee.com/api/v5/repos/{owner}/{repo}"
    try:
        releases = _gitee_fetch_releases(api_base, token)
        release = next((item for item in releases if item.get("tag_name") == tag), None)
        if not release:
            return None
        assets = _gitee_fetch_assets(api_base, token, release["id"])
    except (KeyError, requests.exceptions.RequestException):
        return None
    return release, {
        "token": token,
        "owner": owner,
        "repo": repo,
        "tag": tag,
        "api_base": api_base,
        "release_id": release["id"],
        "existing": assets,
    }


def _verify_latest_manifest(version: str, github_assets: dict,
                            release_notes: str, report=None) -> bool:
    """Cross-check latest.json against one verified GitHub Release."""
    report = report or (lambda message: print(f"  {message}"))
    latest_path = BASE_DIR / "latest.json"
    try:
        data = json.loads(latest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        report(f"[错误] latest.json 无法读取: {exc}")
        return False

    ok = True
    if data.get("version") != version:
        report(f"[错误] latest.json 版本为 {data.get('version')!r}，预期 {version!r}")
        ok = False

    expected_downloads = {
        "windows": f"https://github.com/yaoyouzhong/boss-resume-filter/releases/download/v{version}/BOSS_ResumeFilter.exe",
        "macos": f"https://github.com/yaoyouzhong/boss-resume-filter/releases/download/v{version}/BOSS_ResumeFilter_mac.zip",
        "macos_dmg": f"https://github.com/yaoyouzhong/boss-resume-filter/releases/download/v{version}/BOSS_ResumeFilter.dmg",
    }
    if data.get("downloads") != expected_downloads:
        report("[错误] latest.json GitHub 下载地址与版本不一致")
        ok = False

    expected_cn = {
        "windows": f"https://gitee.com/yaoyouzhong/boss-resume-filter/releases/download/v{version}/BOSS_ResumeFilter.exe",
        "macos": f"https://gitee.com/yaoyouzhong/boss-resume-filter/releases/download/v{version}/BOSS_ResumeFilter_mac.zip",
        "macos_dmg": f"https://gitee.com/yaoyouzhong/boss-resume-filter/releases/download/v{version}/BOSS_ResumeFilter.dmg",
    }
    if data.get("downloads_cn") != expected_cn:
        report("[错误] latest.json Gitee 下载地址与版本不一致")
        ok = False

    manifest_assets = data.get("assets") or {}
    asset_names = {
        "windows": "BOSS_ResumeFilter.exe",
        "macos": "BOSS_ResumeFilter_mac.zip",
        "macos_dmg": "BOSS_ResumeFilter.dmg",
    }
    for key, name in asset_names.items():
        remote = github_assets.get(name) or {}
        expected_sha = _asset_digest_sha256(remote)
        try:
            expected_size = int(remote.get("size") or 0)
            actual_size = int((manifest_assets.get(key) or {}).get("size") or 0)
        except (TypeError, ValueError):
            expected_size = actual_size = 0
        actual_sha = (manifest_assets.get(key) or {}).get("sha256")
        if expected_size <= 0 or actual_size != expected_size or actual_sha != expected_sha:
            report(f"[错误] latest.json {key} 的 size/SHA256 与 GitHub Release 不一致")
            ok = False

    if _normalize_markdown(data.get("release_notes", "")) != _normalize_markdown(release_notes):
        report("[错误] latest.json release_notes 与公开 Release 不一致")
        ok = False

    if ok:
        report("[OK] latest.json 版本、下载地址、发布说明和附件完整性元数据一致")
    return ok


def _verify_release_remote_state(version: str) -> bool:
    """Run the read-only post-release verification used by --verify-release."""
    tag = f"v{version}"
    print(f"\n>>> 只读核验正式发布 {tag}")
    ok = True

    origin_master = _remote_ref_commit("origin", "refs/heads/master")
    gitee_master = _remote_ref_commit("gitee", "refs/heads/master")
    origin_tag = _remote_tag_commit("origin", tag)
    gitee_tag = _remote_tag_commit("gitee", tag)
    if not origin_master or not gitee_master or origin_master != gitee_master:
        print("  [错误] GitHub/Gitee master 未同步")
        ok = False
    else:
        print(f"  [OK] GitHub/Gitee master 一致: {origin_master[:12]}")
    if not origin_tag or not gitee_tag or origin_tag != gitee_tag:
        print("  [错误] GitHub/Gitee tag 缺失或指向不一致")
        ok = False
    else:
        print(f"  [OK] GitHub/Gitee {tag} 一致: {origin_tag[:12]}")

    expected_title, expected_notes = _extract_changelog_release(version)
    github_release = _get_github_release_info(tag)
    if not github_release:
        print(f"  [错误] GitHub Release {tag} 不存在或无法读取")
        return False
    if github_release.get("isDraft"):
        print(f"  [错误] GitHub Release {tag} 仍为草稿")
        ok = False
    if github_release.get("tagName") != tag:
        print("  [错误] GitHub Release tagName 不一致")
        ok = False
    if github_release.get("name") != expected_title:
        print("  [错误] GitHub Release 标题与 CHANGELOG 不一致")
        ok = False
    if _normalize_markdown(github_release.get("body", "")) != _normalize_markdown(expected_notes):
        print("  [错误] GitHub Release 正文与 CHANGELOG 不一致")
        ok = False

    github_assets = _verify_github_release_assets_complete(tag)
    if github_assets is None:
        return False

    gitee_result = _get_gitee_release_read_only(version)
    if not gitee_result:
        print(f"  [错误] Gitee Release {tag} 不存在或无法读取")
        return False
    gitee_release, release_cache = gitee_result
    if gitee_release.get("name") != expected_title:
        print("  [错误] Gitee Release 标题与 CHANGELOG 不一致")
        ok = False
    if _normalize_markdown(gitee_release.get("body", "")) != _normalize_markdown(expected_notes):
        print("  [错误] Gitee Release 正文与 CHANGELOG 不一致")
        ok = False
    if not _verify_gitee_release_assets_complete(tag, github_assets, release_cache):
        ok = False
    if not _verify_latest_manifest(version, github_assets, expected_notes):
        ok = False

    print(f"\n>>> {tag} 远端发布核验{'通过' if ok else '失败'}")
    return ok


def _gitee_asset_matches_local(local_path, remote_asset, owner, repo, tag, token=None):
    """Return True when a Gitee Release asset has identical file content."""
    local_size = local_path.stat().st_size
    remote_size = int(remote_asset.get("size") or 0)
    if remote_size != local_size:
        return False, f"大小不一致 (本地 {local_size} vs 远端 {remote_size})"

    local_sha = _sha256_file(local_path)
    remote_sha = _asset_digest_sha256(remote_asset)
    if remote_sha:
        if remote_sha == local_sha:
            return True, f"SHA256 一致 ({local_size} bytes)"
        return False, "SHA256 不一致"

    if _latest_json_asset_matches_local(local_path):
        return True, f"latest.json 元数据一致 ({local_size} bytes)"

    url = _gitee_asset_url(owner, repo, tag, local_path.name)
    try:
        remote_sha = _remote_file_sha256(url, token=token)
    except requests.exceptions.RequestException as e:
        detail = release_retry.redact_sensitive_text(e, (token or "",))
        return False, f"远端校验失败 ({detail})"
    if remote_sha == local_sha:
        return True, f"SHA256 一致 ({local_size} bytes)"
    return False, "SHA256 不一致"


def _latest_json_asset_info(path_or_name):
    """Return asset metadata from latest.json for a release artifact, if available."""
    key = _release_asset_key(path_or_name)
    if not key:
        return None
    latest_path = BASE_DIR / "latest.json"
    if not latest_path.exists():
        return None
    try:
        data = json.loads(latest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data.get("assets", {}).get(key)


def _latest_json_asset_matches_local(local_path):
    """Use latest.json asset metadata as a cheap local equality check."""
    asset_info = _latest_json_asset_info(local_path)
    if not asset_info:
        return False
    try:
        expected_size = int(asset_info.get("size"))
    except (TypeError, ValueError):
        return False
    expected_sha = _asset_digest_sha256(asset_info)
    if not expected_sha:
        return False
    return (
        expected_size == local_path.stat().st_size
        and expected_sha == _sha256_file(local_path)
    )


def _latest_json_asset_matches_remote_asset(path_or_name, remote_asset):
    """Use latest.json metadata to prove a remote release asset is unchanged."""
    asset_info = _latest_json_asset_info(path_or_name)
    if not asset_info or not remote_asset:
        return False
    try:
        expected_size = int(asset_info.get("size"))
        remote_size = int(remote_asset.get("size") or 0)
    except (TypeError, ValueError):
        return False
    expected_sha = _asset_digest_sha256(asset_info)
    remote_sha = _asset_digest_sha256(remote_asset)
    return (
        expected_size > 0
        and expected_size == remote_size
        and bool(expected_sha)
        and expected_sha == remote_sha
    )


def _gitee_asset_can_reuse_github_metadata(filename, gitee_asset, github_asset):
    """Return True when existing Gitee asset can be reused without downloading."""
    if filename not in gitee_asset.get("name", filename):
        return False
    if not _latest_json_asset_matches_remote_asset(filename, github_asset):
        return False
    try:
        gitee_size = int(gitee_asset.get("size") or 0)
        github_size = int(github_asset.get("size") or 0)
    except (TypeError, ValueError):
        return False
    return gitee_size > 0 and gitee_size == github_size


def _release_asset_key(path_or_name):
    """Map release artifact filename to latest.json assets key."""
    name = Path(path_or_name).name
    if name == "BOSS_ResumeFilter.exe":
        return "windows"
    if name == "BOSS_ResumeFilter_mac.zip":
        return "macos"
    if name == "BOSS_ResumeFilter.dmg":
        return "macos_dmg"
    return None


def _current_platform_update_artifact_names():
    """Return artifact names whose changes require rebuilding the opposite platform."""
    if IS_MAC:
        return {"BOSS_ResumeFilter.dmg", "BOSS_ResumeFilter_mac.zip"}
    return {"BOSS_ResumeFilter.exe"}


def _release_asset_metadata(extra_paths=None):
    """Build update metadata for current-platform artifacts plus explicitly provided files."""
    if IS_WIN:
        assets = {"windows": DIST_DIR / "BOSS_ResumeFilter.exe"}
    elif IS_MAC:
        assets = {
            "macos": DIST_DIR / "BOSS_ResumeFilter_mac.zip",
            "macos_dmg": DIST_DIR / "BOSS_ResumeFilter.dmg",
        }
    else:
        assets = {}

    for path in extra_paths or []:
        key = _release_asset_key(path)
        if key:
            assets[key] = Path(path)

    metadata = {}
    for key, path in assets.items():
        if not path.exists() or not path.is_file():
            continue
        metadata[key] = {
            "size": path.stat().st_size,
            "sha256": _sha256_file(path),
        }
    return metadata


def _release_asset_metadata_from_remote_assets(remote_assets):
    """Build update metadata from GitHub Release asset JSON when digest is available."""
    metadata = {}
    for asset in remote_assets:
        key = _release_asset_key(asset.get("name", ""))
        if not key:
            continue
        sha256 = _asset_digest_sha256(asset)
        try:
            size = int(asset.get("size") or 0)
        except (TypeError, ValueError):
            size = 0
        if size > 0 and sha256:
            metadata[key] = {
                "size": size,
                "sha256": sha256,
            }
    return metadata


def _required_update_asset_keys():
    """Assets used by automatic/manual update manifests."""
    return {"windows", "macos"}


def _assert_update_asset_metadata_complete(asset_metadata):
    missing = [
        key for key in sorted(_required_update_asset_keys())
        if not asset_metadata.get(key, {}).get("size")
        or not asset_metadata.get(key, {}).get("sha256")
    ]
    if missing:
        print(f"[错误] latest.json 缺少更新包完整性元数据: {', '.join(missing)}")
        print("请先确保 Windows EXE 和 macOS ZIP 均已构建并可从 GitHub Release 下载。")
        sys.exit(1)


def _extract_changelog_release(version):
    """从 CHANGELOG.md 提取当前版本的 Release 标题和正文。"""
    changelog_path = BASE_DIR / "CHANGELOG.md"
    if not changelog_path.exists():
        print("[错误] CHANGELOG.md 不存在，Release 必须先写本地更新日志")
        sys.exit(1)

    content = changelog_path.read_text(encoding="utf-8")
    pattern = re.compile(
        rf"^##\s+(v{re.escape(version)}[^\n]*)\n(?P<body>.*?)(?=^##\s+v|\Z)",
        re.MULTILINE | re.DOTALL,
    )
    match = pattern.search(content)
    if not match:
        print(f"[错误] CHANGELOG.md 中未找到 v{version} 段落")
        print("请先在 CHANGELOG.md 顶部补充本版本发布说明，再执行 --release。")
        sys.exit(1)

    title = match.group(1).strip()
    body = match.group("body").strip()
    if not body:
        print(f"[错误] CHANGELOG.md 中 v{version} 段落正文为空")
        sys.exit(1)

    required_sections = CHANGELOG_REQUIRED_SECTIONS
    found_sections = re.findall(r"^###\s+(.+?)\s*$", body, re.MULTILINE)

    # 至少有一个分类
    present = [section for section in required_sections if section in found_sections]
    if not present:
        print(f"[错误] CHANGELOG.md 中 v{version} 段落缺少发布分类")
        print(f"至少需要以下分类之一：{', '.join(required_sections)}")
        sys.exit(1)

    # 检查存在的分类是否按规范顺序排列
    present_positions = [(section, found_sections.index(section)) for section in required_sections if section in found_sections]
    positions = [pos for _, pos in present_positions]
    if positions != sorted(positions):
        print(f"[错误] CHANGELOG.md 中 v{version} 段落发布分类顺序不正确")
        print("当前顺序：")
        for section in found_sections:
            print(f"  - {section}")
        print("\n要求顺序：")
        for section in required_sections:
            print(f"  - {section}")
        sys.exit(1)

    print(f"  [OK] Release 标题来自 CHANGELOG.md：{title}")
    print("  [OK] Release 说明来自 CHANGELOG.md 当前版本段落")
    return title, body


def _sync_release_notes():
    """从已推送的 CHANGELOG 恢复同步 GitHub/Gitee Release 说明。"""
    def _run_external(args, label, postcondition=None):
        result = release_retry.run_cli_with_retries(
            lambda command, **kwargs: subprocess.run(
                command,
                cwd=BASE_DIR,
                text=True,
                encoding="utf-8",
                errors="replace",
                **kwargs,
            ),
            args,
            label,
            postcondition=postcondition,
        )
        if result.returncode != 0:
            print(f"错误: {label}失败：{release_retry.command_detail(result)}")
            sys.exit(1)
        return result

    version = _read_version()
    tag = f"v{version}"
    release_title, release_notes = _extract_changelog_release(version)

    branch = subprocess.run(
        ["git", "branch", "--show-current"], cwd=BASE_DIR,
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    has_changes, status_text = _git_status()
    if branch != "master" or has_changes:
        print("错误: Release 说明恢复只能从干净的 master 执行")
        if status_text:
            print(status_text)
        sys.exit(1)
    _run_external(["git", "fetch", "origin", "master"], "拉取 GitHub master")
    _run_external(["git", "fetch", "gitee", "master"], "拉取 Gitee master")
    head = _local_ref_commit("HEAD")
    origin_master = _remote_ref_commit("origin", "refs/heads/master")
    gitee_master = _remote_ref_commit("gitee", "refs/heads/master")
    if not head or head != origin_master or head != gitee_master:
        print("错误: 本地、GitHub 和 Gitee master 必须先完全一致")
        sys.exit(1)

    latest = json.loads((BASE_DIR / "latest.json").read_text(encoding="utf-8"))
    if str(latest.get("version", "")).lstrip("v") != version:
        print("错误: latest.json 版本与当前版本不一致")
        sys.exit(1)
    if _normalize_markdown(latest.get("release_notes", "")) != _normalize_markdown(release_notes):
        print("错误: 请先同步、提交并推送 latest.json.release_notes")
        sys.exit(1)

    token = os.environ.get("GITEE_TOKEN", "")
    if not token or not _gitee_ping(token):
        print("错误: Gitee 访问预检未通过，未修改任何公开 Release")
        sys.exit(1)
    owner = "yaoyouzhong"
    repo = "boss-resume-filter"
    api_base = f"https://gitee.com/api/v5/repos/{owner}/{repo}"
    try:
        gitee_release = _gitee_session().get(
            f"{api_base}/releases/tags/{tag}",
            params={"access_token": token},
            timeout=15,
        )
    except requests.exceptions.RequestException as e:
        detail = release_retry.redact_sensitive_text(e, (token,))
        print(f"错误: Gitee Release 预检失败: {detail}")
        sys.exit(1)
    if gitee_release.status_code != 200:
        print(f"错误: Gitee Release {tag} 不存在或查询失败 ({gitee_release.status_code})")
        sys.exit(1)
    release_id = gitee_release.json()["id"]

    print(f"\n>>> 同步 Release 说明到 {tag}")
    print(f"  标题: {release_title}")
    print(f"  正文: {len(release_notes)} 字符\n")

    # 写入临时文件（避免 Windows GBK 编码问题）
    _notes_file = BASE_DIR / "_release_notes_tmp.txt"
    _notes_file.write_text(release_notes, encoding="utf-8")

    # 1. GitHub Release
    try:
        if _get_github_release_info(tag) is None:
            print(f"[错误] GitHub Release {tag} 不存在，请先完成正式发布")
            sys.exit(1)

        _run_external(
            ["gh", "release", "edit", tag,
             "--title", release_title,
             "--notes-file", str(_notes_file)],
            f"更新 GitHub Release {tag}",
            postcondition=lambda: (
                (current := _get_github_release_info(tag)) is not None
                and current.get("name") == release_title
                and _normalize_markdown(current.get("body", ""))
                == _normalize_markdown(release_notes)
            ),
        )
        print(f"  [OK] GitHub Release {tag} 说明已更新")
    finally:
        _notes_file.unlink(missing_ok=True)

    # 2. Gitee Release
    session = _gitee_session(retries=0)
    last_error = "unknown"
    for attempt in range(4):
        try:
            resp = session.patch(
                f"{api_base}/releases/{release_id}",
                params={"access_token": token},
                json={
                    "tag_name": tag,
                    "name": release_title,
                    "body": release_notes,
                },
                timeout=30,
            )
            resp.raise_for_status()
            print(f"  [OK] Gitee Release {tag} 说明已更新")
            break
        except requests.exceptions.RequestException as exc:
            last_error = release_retry.redact_sensitive_text(exc, (token,))
            try:
                current = session.get(
                    f"{api_base}/releases/tags/{tag}",
                    params={"access_token": token},
                    timeout=15,
                )
                current.raise_for_status()
                data = current.json()
                if (
                    data.get("name") == release_title
                    and _normalize_markdown(data.get("body", ""))
                    == _normalize_markdown(release_notes)
                ):
                    print(f"  [OK] Gitee 已更新 {tag}，忽略丢失的 API 响应")
                    break
            except requests.exceptions.RequestException:
                pass
            status = getattr(getattr(exc, "response", None), "status_code", None)
            if (
                status is not None
                and 400 <= status < 500
                and status not in {408, 429}
            ):
                print(f"  [错误] Gitee Release 更新失败: HTTP {status}")
                sys.exit(1)
            if attempt >= 3:
                print(f"  [错误] Gitee API 请求失败: {last_error}")
                sys.exit(1)
            delay = 2 * (attempt + 1)
            print(
                f"  [重试] Gitee Release 更新瞬时失败，准备第 "
                f"{attempt + 1}/3 次重试（{delay}s 后）"
            )
            time.sleep(delay)

    if not _verify_release_remote_state(version):
        print("  [错误] Release 说明同步后验收未通过")
        sys.exit(1)

    print(f"\n>>> Release 说明同步完成")


def _check_readme_release(version, strict_details=False):
    """验证 README.md 已同步当前发布版本。"""
    readme_path = BASE_DIR / "README.md"
    if not readme_path.exists():
        print("[错误] README.md 不存在，Release 必须先更新项目主页说明")
        sys.exit(1)

    content = readme_path.read_text(encoding="utf-8")
    required_version_text = f"当前发布版本：v{version}"
    version_label_pattern = re.compile(
        rf"^>\s*当前发布版本：.*v{re.escape(version)}.*$",
        re.MULTILINE,
    )
    if not version_label_pattern.search(content):
        print(f"[错误] README.md 未同步当前发布版本：v{version}")
        print(f"请在 README.md 顶部加入或更新：> {required_version_text}")
        sys.exit(1)

    release_heading = re.compile(
        rf"^###\s+(?:v{re.escape(version)}(?:\s|$)|v2\.8\s+补丁版本\s*$)",
        re.MULTILINE,
    )
    if not release_heading.search(content):
        print(f"[错误] README.md 未找到 v{version} 功能摘要小节")
        print(f"请在 README.md 功能特性中补充：### v{version} 新增功能")
        sys.exit(1)

    gui_version_text = f"gui_main.py            # 图形界面主程序（v{version}）"
    if gui_version_text not in content:
        print(f"[错误] README.md 项目结构中的 gui_main.py 版本未同步为 v{version}")
        print(f"请将项目结构中的 gui_main.py 标注更新为：# 图形界面主程序（v{version}）")
        sys.exit(1)

    print(f"  [OK] README.md 已同步 v{version} 项目主页说明")

    # 核对 CHANGELOG 和 README 的条目数量和分类一致
    changelog_path = BASE_DIR / "CHANGELOG.md"
    if changelog_path.exists():
        changelog_text = changelog_path.read_text(encoding="utf-8")
        readme_text = content  # already read above

        # 提取 CHANGELOG 中该版本的段落
        cl_match = re.search(
            rf"^## v{re.escape(version)}.*?\n(.*?)(?=^## v|\Z)",
            changelog_text, re.MULTILINE | re.DOTALL,
        )
        # 提取 README 中该版本的段落
        rm_match = re.search(
            rf"^### v{re.escape(version)}.*?\n(.*?)(?=^### v|^### 筛选规则|\Z)",
            readme_text, re.MULTILINE | re.DOTALL,
        )
        if cl_match and rm_match:
            cl_section = cl_match.group(1)
            rm_section = rm_match.group(1)
            cl_sections = _parse_changelog_sections(cl_section)
            rm_sections = _parse_readme_release_sections(rm_section)

            for category in ["新增功能", "体验优化", "问题修复"]:
                cl_titles = _extract_release_entry_titles(cl_sections.get(category, []))
                rm_titles = _extract_release_entry_titles(rm_sections.get(category, []))
                cl_count = len(cl_titles)
                rm_count = len(rm_titles)

                if cl_count != rm_count:
                    level = "错误" if strict_details else "警告"
                    print(f"[{level}] README.md v{version} {category} 条目数（{rm_count}）与 CHANGELOG（{cl_count}）不一致")
                    print(f"请复核 README.md 当前版本摘要是否需要同步 CHANGELOG.md 中 v{version} {category} 的条目")
                    if strict_details:
                        sys.exit(1)
                    continue
                if cl_titles != rm_titles:
                    level = "错误" if strict_details else "警告"
                    print(f"[{level}] README.md v{version} {category} 条目标题与 CHANGELOG 不一致")
                    print("CHANGELOG：")
                    for title in cl_titles:
                        print(f"  - {title}")
                    print("README：")
                    for title in rm_titles:
                        print(f"  - {title}")
                    if strict_details:
                        sys.exit(1)

            if strict_details:
                print(f"  [OK] README.md v{version} 条目标题与 CHANGELOG 一致")
            else:
                print(f"  [OK] README.md v{version} 当前版本摘要已存在；逐条镜像检查为提示项")


def _check_latest_json_release_notes(version, strict=False):
    """验证 latest.json 内置 release_notes 与 CHANGELOG 当前版本一致。"""
    latest_path = BASE_DIR / "latest.json"
    if not latest_path.exists():
        print("  [跳过] latest.json release_notes 检查：文件不存在")
        return

    try:
        data = json.loads(latest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        print(f"[错误] latest.json 解析失败：{e}")
        sys.exit(1)

    latest_version = str(data.get("version", "")).lstrip("v")
    if latest_version != version:
        print(
            f"  [跳过] latest.json release_notes 检查：latest.json 为 v{latest_version}，"
            f"当前源码为 v{version}"
        )
        return

    expected_title, expected_body = _extract_changelog_release(version)
    actual_body = data.get("release_notes", "")
    if _normalize_markdown(actual_body) != _normalize_markdown(expected_body):
        level = "错误" if strict else "警告"
        print(f"[{level}] latest.json release_notes 与 CHANGELOG.md v{version} 不一致")
        print("请在发布前运行发布流程或同步 latest.json，确保内置更新说明来自当前 CHANGELOG。")
        if strict:
            sys.exit(1)
        print("  [继续] 默认检查模式下仅提示；使用 --strict-changelog 可将此项作为硬门禁")
        return

    print(f"  [OK] latest.json release_notes 已同步 CHANGELOG.md v{version}（{expected_title}）")


def _version_sort_key(version):
    return tuple(int(part) for part in version.split("."))


def _check_version_history_integrity():
    """验证 CHANGELOG 含全部历史版本，README 只保留最近 3 个版本。"""
    # 从 Git tags 获取所有历史版本
    r = subprocess.run(
        ["git", "tag", "-l", "v*"],
        capture_output=True, text=True, cwd=BASE_DIR
    )
    if r.returncode != 0:
        print("  [跳过] 版本历史检查：无法获取 Git tags")
        return

    all_tags = [line.strip() for line in r.stdout.splitlines() if line.strip()]
    if not all_tags:
        print("  [跳过] 版本历史检查：没有找到任何 tag")
        return

    # 过滤出有效的公开版本标签（v1.0, v2.8.12 等；v2.9.0 属非法历史 tag）
    versions = [tag[1:] for tag in all_tags if _is_valid_release_tag(tag)]  # 去掉 'v' 前缀

    # 发布前检查发生在新 tag 创建之前，当前源码版本也必须纳入“最近 3 个版本”。
    # 否则 README 同时保留当前版本和最近 3 个旧 tag 时会超过上限，形成无法满足的门禁。
    current_version = _read_version()
    if current_version not in versions:
        versions.append(current_version)

    if not versions:
        print("  [跳过] 版本历史检查：没有找到有效的语义化版本")
        return

    # 检查 CHANGELOG
    changelog_path = BASE_DIR / "CHANGELOG.md"
    if not changelog_path.exists():
        print("[错误] CHANGELOG.md 不存在")
        sys.exit(1)

    changelog_text = changelog_path.read_text(encoding="utf-8")

    # 识别"已合并"的版本范围说明
    # 例如：v2.8.1 至 v2.8.7 为内部修复版本，相关改进已合并至 v2.8.8 及后续版本
    merged_versions = set()
    merged_pattern = re.compile(r"v(\d+\.\d+\.\d+)\s*至\s*v(\d+\.\d+\.\d+)\s*为内部修复版本")
    for match in merged_pattern.finditer(changelog_text):
        start_ver = match.group(1)
        end_ver = match.group(2)
        # 解析版本号并生成范围内的所有版本
        start_parts = [int(x) for x in start_ver.split('.')]
        end_parts = [int(x) for x in end_ver.split('.')]
        if start_parts[:2] == end_parts[:2]:  # 相同的主版本和次版本
            for patch in range(start_parts[2], end_parts[2] + 1):
                merged_versions.add(f"{start_parts[0]}.{start_parts[1]}.{patch}")

    missing_in_changelog = []
    for ver in versions:
        pattern = rf"^##\s+v{re.escape(ver)}\b"
        if not re.search(pattern, changelog_text, re.MULTILINE) and ver not in merged_versions:
            missing_in_changelog.append(ver)

    # 报告结果
    if missing_in_changelog:
        print(f"[错误] CHANGELOG.md 缺少以下 {len(missing_in_changelog)} 个历史版本：")
        for ver in sorted(missing_in_changelog, key=_version_sort_key):
            print(f"  - v{ver}")
        print("\n请从 Git 历史恢复这些版本的发布说明，或确认它们已被合并到其他版本。")
        sys.exit(1)

    # README 是项目主页，只保留最近 3 个版本摘要，完整历史以 CHANGELOG.md 为准。
    readme_path = BASE_DIR / "README.md"
    if not readme_path.exists():
        print("[错误] README.md 不存在")
        sys.exit(1)

    readme_text = readme_path.read_text(encoding="utf-8")
    recent_versions = sorted(versions, key=_version_sort_key, reverse=True)[:3]
    missing_recent_in_readme = [
        ver for ver in recent_versions
        if not re.search(rf"^###\s+v{re.escape(ver)}\b", readme_text, re.MULTILINE)
    ]
    if missing_recent_in_readme:
        print(f"[错误] README.md 缺少最近 3 个版本中的以下版本摘要：")
        for ver in sorted(missing_recent_in_readme, key=_version_sort_key, reverse=True):
            print(f"  - v{ver}")
        print("\nREADME.md 只需保留最近 3 个版本；更早版本请引导用户查看 CHANGELOG.md。")
        sys.exit(1)

    # 检查 README 是否保留了过多详细版本段落（最多 3 个）
    all_readme_versions = [
        v for v in re.findall(
            r"^###\s+v(\d+\.\d+(?:\.\d+)?)\b(?!.*及更早版本)",
            readme_text, re.MULTILINE
        )
    ]
    excess = len(all_readme_versions) - 3
    if excess > 0:
        print(
            f"[错误] README.md 保留了 {len(all_readme_versions)} 个详细版本段落，"
            f"超过上限 3 个（多 {excess} 个）"
        )
        print("请将更早版本合并为「vX.Y 及更早版本 → 完整版本历史见 CHANGELOG.md」")
        sys.exit(1)

    print(
        f"  [OK] 版本历史完整性检查通过：CHANGELOG 含全部 {len(versions)} 个历史版本，"
        f"README 保留最近 {len(recent_versions)} 个版本摘要"
    )


# ---------------------------------------------------------------------------
#  Release 子步骤（仅 --release 模式调用）
# ---------------------------------------------------------------------------

def _git_status():
    """返回 (has_changes, status_text)"""
    r = subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True, cwd=BASE_DIR)
    status_text = r.stdout.rstrip()
    return bool(status_text), status_text


def _local_ref_commit(ref: str) -> str | None:
    """Return the commit for a local ref, or None when the ref does not exist."""
    result = subprocess.run(
        ["git", "rev-list", "-n", "1", ref],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=BASE_DIR,
    )
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def _remote_tag_commit(remote: str, tag: str) -> str | None:
    """Return a remote tag commit without fetching or mutating local refs."""
    result = subprocess.run(
        ["git", "ls-remote", "--tags", remote,
         f"refs/tags/{tag}", f"refs/tags/{tag}^{{}}"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=BASE_DIR,
    )
    if result.returncode != 0:
        print(f"[错误] 无法读取 {remote} 的 {tag}: {result.stderr.strip()}")
        sys.exit(1)

    direct = peeled = None
    for line in result.stdout.splitlines():
        parts = line.split()
        if len(parts) != 2:
            continue
        commit, ref = parts
        if ref.endswith("^{}"):
            peeled = commit
        elif ref == f"refs/tags/{tag}":
            direct = commit
    return peeled or direct


def _abort_tag_conflict(tag: str, source: str, actual: str, expected: str) -> None:
    """Stop before a release can rewrite an existing tag."""
    print(f"[错误] {source} 的 {tag} 指向 {actual[:12]}，当前提交为 {expected[:12]}")
    print("已公开或已存在的 tag 不得移动；请发布更高补丁版本，或人工处理未公开的冲突 tag。")
    sys.exit(1)


def _assert_release_tag_reusable(version: str, will_create_commit: bool = False) -> None:
    """Allow a release tag only when absent or already on the current commit."""
    tag = f"v{version}"
    head_commit = _local_ref_commit("HEAD")
    if not head_commit:
        print("[错误] 无法读取当前 HEAD，停止发布")
        sys.exit(1)

    local_commit = _local_ref_commit(tag)
    remote_commit = _remote_tag_commit("origin", tag)
    if will_create_commit and (local_commit or remote_commit):
        print(f"[错误] {tag} 已存在，但当前发布还会创建新提交")
        print("不能让既有 tag 改指向新提交；请发布更高补丁版本。")
        sys.exit(1)
    if local_commit and local_commit != head_commit:
        _abort_tag_conflict(tag, "本地", local_commit, head_commit)
    if remote_commit and remote_commit != head_commit:
        _abort_tag_conflict(tag, "origin", remote_commit, head_commit)


def _git_commit(version, allowed_paths=None):
    """只提交明确允许的发布相关变更"""
    has_changes, status_text = _git_status()
    if not has_changes:
        print("  [跳过] 没有未提交的变更")
        return

    allowed = set(allowed_paths or [])
    changed = []
    for line in status_text.splitlines():
        # porcelain: XY path 或 XY old -> new
        path = line[3:]
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        changed.append(path)

    unexpected = [path for path in changed if path not in allowed]
    if unexpected:
        print("[错误] Release 模式拒绝自动提交非发布文件：\n")
        for line in status_text.splitlines():
            print(f"  {line}")
        print("\n请先手动提交这些变更，或只使用 --version 让发布脚本更新 gui_main.py。")
        sys.exit(1)

    print(f"\n  待提交的变更：\n")
    for line in status_text.splitlines():
        print(f"    {line}")

    subprocess.run(["git", "add", *sorted(allowed)], cwd=BASE_DIR, check=True)
    msg = f"release: v{version}"
    subprocess.run(["git", "commit", "-m", msg], cwd=BASE_DIR, check=True)
    print(f"  [OK] 已提交：{msg}")


def update_latest_json(version, release_notes, downloads_cn=None, quiet=False,
                       asset_metadata=None, require_complete_assets=False):
    """更新 latest.json 文件（供 Gitee 镜像使用）

    Args:
        downloads_cn: Gitee 国内下载链接字典 {"windows": url, "macos": url}
    """
    from datetime import date

    latest_path = BASE_DIR / "latest.json"
    existing_data = None
    if latest_path.exists():
        try:
            existing_data = json.loads(latest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            existing_data = None

    release_date = date.today().isoformat()
    if existing_data and existing_data.get("version") == version and existing_data.get("release_date"):
        release_date = existing_data["release_date"]

    assets = asset_metadata or _release_asset_metadata()
    if asset_metadata is None and existing_data and existing_data.get("version") == version:
        assets = {**(existing_data.get("assets") or {}), **assets}

    latest_data = {
        "version": version,
        "release_date": release_date,
        "downloads": {
            "windows": f"https://github.com/yaoyouzhong/boss-resume-filter/releases/download/v{version}/BOSS_ResumeFilter.exe",
            "macos": f"https://github.com/yaoyouzhong/boss-resume-filter/releases/download/v{version}/BOSS_ResumeFilter_mac.zip",
            "macos_dmg": f"https://github.com/yaoyouzhong/boss-resume-filter/releases/download/v{version}/BOSS_ResumeFilter.dmg"
        },
        "assets": assets,
        "release_notes": release_notes
    }

    if require_complete_assets:
        _assert_update_asset_metadata_complete(latest_data["assets"])

    if downloads_cn is not None:
        latest_data["downloads_cn"] = downloads_cn
    elif existing_data and existing_data.get("downloads_cn"):
        latest_data["downloads_cn"] = existing_data["downloads_cn"]

    if existing_data == latest_data:
        if not quiet:
            print(f"  [跳过] latest.json 无变化")
        return False

    with open(latest_path, 'w', encoding='utf-8') as f:
        json.dump(latest_data, f, ensure_ascii=False, indent=2)

    if not quiet:
        print(f"  [OK] 已更新 latest.json (v{version})")
    return True


def _git_tag(version):
    """创建本地 tag；同名 tag 仅在已指向当前提交时允许续跑。"""
    tag = f"v{version}"
    head_commit = _local_ref_commit("HEAD")
    if not head_commit:
        print("[错误] 无法读取当前 HEAD，停止创建 tag")
        sys.exit(1)
    old_tag_commit = _local_ref_commit(tag)
    if old_tag_commit:
        if old_tag_commit != head_commit:
            _abort_tag_conflict(tag, "本地", old_tag_commit, head_commit)
        print(f"  [跳过] 本地 tag 已指向当前提交: {tag}")
        return old_tag_commit

    subprocess.run(["git", "tag", tag], cwd=BASE_DIR, check=True)
    print(f"  [OK] 已创建本地 tag: {tag}")
    return None


def _git_push(version, auto=False):
    """推送 master 和 tag 到远程"""
    tag = f"v{version}"
    head_commit = _local_ref_commit("HEAD")
    local_tag_commit = _local_ref_commit(tag)
    remote_tag_commit = _remote_tag_commit("origin", tag)
    if not head_commit or local_tag_commit != head_commit:
        print(f"[错误] 本地 {tag} 未指向当前 HEAD，停止推送")
        sys.exit(1)
    if remote_tag_commit and remote_tag_commit != head_commit:
        _abort_tag_conflict(tag, "origin", remote_tag_commit, head_commit)

    print(f"\n{'='*60}")
    print(f"  即将推送到远程：")
    print(f"    1. git push origin master")
    if remote_tag_commit:
        print(f"    2. {tag} 已存在且提交一致，跳过 tag 推送")
    else:
        print(f"    2. git push origin {tag}")
    print(f"{'='*60}")

    if auto:
        print("  [--auto] 跳过确认，直接推送")
    else:
        resp = input("\n  确认推送？[y/N] ").strip().lower()
        if resp != 'y':
            print("  已取消推送。tag 和提交保留在本地，可稍后手动推送。")
            sys.exit(0)

    # 推送 master，带重试
    for attempt in range(3):
        try:
            subprocess.run(["git", "push", "origin", "master"], cwd=BASE_DIR, check=True)
            print(f"  [OK] master 已推送")
            break
        except subprocess.CalledProcessError as e:
            if attempt < 2:
                print(f"  [重试] 推送 master 失败 (attempt {attempt+1}/3), 5s 后重试...")
                time.sleep(5)
            else:
                raise

    if remote_tag_commit:
        print(f"  [跳过] origin/{tag} 已指向当前提交")
        return

    # 推送 tag，带重试
    for attempt in range(3):
        try:
            subprocess.run(["git", "push", "origin", tag], cwd=BASE_DIR, check=True)
            print(f"  [OK] {tag} 已推送")
            break
        except subprocess.CalledProcessError as e:
            if attempt < 2:
                print(f"  [重试] 推送 {tag} 失败 (attempt {attempt+1}/3), 5s 后重试...")
                time.sleep(5)
            else:
                raise


def _print_progress(step, total, message, start_time=None):
    """打印进度信息。如果提供 start_time，则在行尾显示耗时。"""
    import time
    if start_time is not None:
        elapsed = time.time() - start_time
        print(f"\n[{step}/{total}] {message} ({elapsed:.1f}s)")
    else:
        print(f"\n[{step}/{total}] {message}")


def _gh_release(version, release_title, release_notes, progress=None,
                enable_gitee=True, enable_ci_sync=True, old_tag_commit=None,
                ci_already_triggered=False):
    """创建/更新 GitHub Release 并上传资源文件

    ci_already_triggered: 如果 CI 已在 PyInstaller 打包前提前触发，跳过内部 CI 触发逻辑，
    但仍等待 CI 完成并将对端产物并行同步到 Gitee。
    """
    import time
    tag = f"v{version}"

    def _sub(msg):
        """子步骤报告：同时打印和更新进度表"""
        print(f"    {msg}", flush=True)
        if progress:
            progress.sub(msg)

    # 按平台选择上传的文件
    if IS_MAC:
        dmg = DIST_DIR / "BOSS_ResumeFilter.dmg"
        mac_zip = DIST_DIR / "BOSS_ResumeFilter_mac.zip"
        artifacts = [(dmg, "DMG"), (mac_zip, "Mac-ZIP")]
    else:
        exe = DIST_DIR / "BOSS_ResumeFilter.exe"
        artifacts = [(exe, "EXE")]

    # 检查 gh CLI
    r = subprocess.run(["gh", "--version"], capture_output=True, cwd=BASE_DIR)
    if r.returncode != 0:
        print("[错误] gh CLI 未安装或未登录，请先运行 gh auth login")
        sys.exit(1)

    _sub('检查 GitHub Release 旧资源...')
    existing = subprocess.run(["gh", "release", "view", tag, "--json", "assets"],
                              capture_output=True, text=True, cwd=BASE_DIR)
    remote_assets = {}
    if existing.returncode == 0 and existing.stdout.strip():
        import json
        assets = json.loads(existing.stdout).get("assets", [])
        remote_assets = {a["name"]: a for a in assets}

    # 创建 Release（如果已存在则跳过创建步骤）
    r = subprocess.run(["gh", "release", "view", tag], capture_output=True, cwd=BASE_DIR)

    # 写入临时文件传递 release notes（避免 Windows GBK 命令行参数编码问题）
    _notes_file = BASE_DIR / "_release_notes_tmp.txt"
    _notes_file.write_text(release_notes, encoding="utf-8")
    try:
        if r.returncode != 0:
            # 创建 Release，带重试
            for attempt in range(3):
                try:
                    subprocess.run(
                        ["gh", "release", "create", tag, "--title", release_title, "--notes-file", str(_notes_file)],
                        cwd=BASE_DIR, check=True)
                    _sub(f'GitHub Release 已创建: {tag}')
                    break
                except subprocess.CalledProcessError as e:
                    # 检查是否因为 release 已存在而失败（并发场景）
                    check_r = subprocess.run(["gh", "release", "view", tag], capture_output=True, cwd=BASE_DIR)
                    if check_r.returncode == 0:
                        # Release 已存在，切换到编辑模式
                        _sub(f'GitHub Release 已存在: {tag}')
                        subprocess.run(
                            ["gh", "release", "edit", tag, "--title", release_title, "--notes-file", str(_notes_file)],
                            cwd=BASE_DIR, check=True)
                        break
                    if attempt < 2:
                        _sub(f'[重试] 创建 Release 失败 (attempt {attempt+1}/3), 5s 后重试...')
                        time.sleep(5)
                    else:
                        raise
        else:
            _sub(f'GitHub Release 已存在: {tag}')
            # 更新 Release，带重试
            for attempt in range(3):
                try:
                    subprocess.run(
                        ["gh", "release", "edit", tag, "--title", release_title, "--notes-file", str(_notes_file)],
                        cwd=BASE_DIR, check=True)
                    break
                except subprocess.CalledProcessError as e:
                    if attempt < 2:
                        _sub(f'[重试] 更新 Release 失败 (attempt {attempt+1}/3), 5s 后重试...')
                        time.sleep(5)
                    else:
                        raise
    finally:
        _notes_file.unlink(missing_ok=True)

    # --- 预计算：哪些本地产物需要上传 ---
    expected_uploads = set()
    for f, label in artifacts:
        if f.exists():
            remote_asset = remote_assets.get(f.name)
            if remote_asset:
                same, reason = _github_asset_matches_local(tag, f, remote_asset)
                if same:
                    continue
            expected_uploads.add(f.name)

    # --- 并行：GH 上传 || (CI 触发 + Gitee 上传) ---
    def _gh_upload_task():
        """上传本地产物到 GitHub Release"""
        uploaded = set()
        to_upload = []
        for f, label in artifacts:
            if not f.exists():
                _sub(f'[GH] [跳过] {f.name}')
                continue
            if f.name not in expected_uploads:
                _sub(f'[GH] [跳过] {f.name} 已一致')
                continue
            to_upload.append(f)

        def _ok(f, name):
            uploaded.add(name)
            _sub(f'[GH] [OK] {name}')
        def _fail(f, error):
            _sub(f'[GH] [失败] {f.name}: {error}')
            raise error

        if to_upload:
            _sub(f'[GH] 上传 {len(to_upload)} 个文件到 GitHub...')
            _run_transfer_batch(
                to_upload,
                "上传 GitHub Release",
                lambda f: _upload_github_release_asset(tag, f, report=_sub),
                _ok,
                _fail,
            )
        _ensure_current_platform_github_assets_match_local(
            tag,
            [f for f, _label in artifacts if f.exists()],
            report=_sub,
        )
        return uploaded

    def _gitee_task():
        """CI 触发（如未提前触发）+ Gitee 本地产物上传"""
        need_ci = False
        old_assets_info = {}

        # CI 触发：仅当未在打包前提前触发、且需要时
        if enable_ci_sync and not ci_already_triggered:
            changed_update = expected_uploads & _current_platform_update_artifact_names()
            if changed_update:
                _sub(f'[CI] 检查跨平台重建: {", ".join(sorted(changed_update))}')
                need_ci, old_assets_info = _trigger_cross_platform_ci(tag, old_tag_commit)
            else:
                _sub('[CI] 跳过跨平台 CI 检查：当前平台更新产物未变化')

        # Gitee 本地上传
        downloads_cn_partial = None
        release_cache = None
        if enable_gitee:
            _sub('[Gitee] 准备上传...')
            release_cache = _gitee_get_release_cache(version, release_title, release_notes)
            if release_cache:
                _sub('[Gitee] 上传本地产物...')
                try:
                    downloads_cn_partial = _gitee_upload_local(
                        version, release_title, release_notes, release_cache)
                except Exception as e:
                    detail = release_retry.redact_sensitive_text(
                        e,
                        (os.environ.get("GITEE_TOKEN", ""),),
                    )
                    _sub(f'[Gitee] [警告] 本地产物上传失败: {detail}')
        else:
            _sub('[Gitee] 跳过：--no-gitee')

        return need_ci, old_assets_info, downloads_cn_partial, release_cache

    # 执行并行
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=2) as pool:
        gh_future = pool.submit(_gh_upload_task)
        gitee_future = pool.submit(_gitee_task)

        uploaded_names = gh_future.result()
        need_ci, old_assets_info, downloads_cn, release_cache = gitee_future.result()

    # CI 已在打包前提前触发，这里标记 need_ci 以便后续等待
    if ci_already_triggered:
        need_ci = True

    # --- 等待 CI 上传 GitHub，然后在本机并行中转对端产物到 Gitee ---
    if enable_ci_sync and release_cache:
        opposite_assets = (
            ["BOSS_ResumeFilter.exe"]
            if IS_MAC
            else ["BOSS_ResumeFilter_mac.zip", "BOSS_ResumeFilter.dmg"]
        )
        if need_ci:
            _sub('等待 CI 完成并上传对端产物到 GitHub...')
            if not _wait_for_github_release_assets(tag, opposite_assets):
                _sub('[错误] 等待 GitHub CI 对端产物超时')
                sys.exit(1)
        else:
            _sub('同步 GitHub 对端产物到 Gitee...')

        github_assets = _verify_github_release_assets_complete(tag, report=_sub)
        if github_assets is None:
            sys.exit(1)
        gitee_sync = _sync_gitee_from_github(
            version,
            release_title,
            release_notes,
            need_wait=False,
            release_cache=release_cache,
        )
        if not gitee_sync:
            _sub('[错误] Gitee 对端产物同步失败')
            sys.exit(1)
        downloads_cn = downloads_cn or {}
        downloads_cn.update(gitee_sync)

        _sub('校验 Gitee Release 附件完整性...')
        if not _verify_release_assets_complete(tag, release_cache=release_cache, report=_sub):
            sys.exit(1)
    else:
        _sub('校验 Release 附件完整性...')
        if not _verify_release_assets_complete(tag, release_cache=release_cache, report=_sub):
            sys.exit(1)

    _sub('Release 发布完成')
    return downloads_cn


def _get_changed_files_since_tag(old_tag_commit=None):
    """获取已有发布 tag 的 commit 到当前 HEAD 之间变更的文件列表。

    用于判断是否需要触发跨平台 CI 重建。中断重入时可传入已有 tag 的 commit，
    与当前 HEAD 对比，只包含本次发布实际变更的文件。
    如果没有旧 commit（首次发布），则用 git describe 查找上一个 tag。
    如果 old_tag_commit 等于 HEAD（中断重入场景），回退到上一个版本 tag。
    """
    import re as _re

    base_ref = None
    if old_tag_commit:
        # 检测中断重入：上次 _git_tag() 已执行但 push 失败，
        # 导致 old_tag_commit == HEAD，diff 为空。回退到上一个版本 tag。
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True, text=True, cwd=BASE_DIR,
        )
        if head.returncode == 0 and head.stdout.strip() == old_tag_commit:
            print("  [信息] old_tag_commit 等于 HEAD（中断重入），回退到上一个版本 tag")
            old_tag_commit = None  # 走下方 fallback 逻辑

    if old_tag_commit:
        base_ref = old_tag_commit
    else:
        # 首次发布或中断重入：查找上一个版本 tag
        r = subprocess.run(
            ["git", "tag", "-l", "v*"],
            capture_output=True, text=True, cwd=BASE_DIR,
        )
        if r.returncode != 0 or not r.stdout.strip():
            return None

        tags = [tag for tag in r.stdout.strip().split('\n') if _is_valid_release_tag(tag)]
        if len(tags) >= 2:
            def _version_key(tag):
                m = _re.match(r'v?(\d+)\.(\d+)(?:\.(\d+))?$', tag)
                return tuple(int(x or 0) for x in m.groups()) if m else (0,)
            tags.sort(key=_version_key)
            base_ref = tags[-2]

    if not base_ref:
        return None

    r = subprocess.run(
        ["git", "diff", "--name-only", f"{base_ref}..HEAD"],
        capture_output=True, text=True, cwd=BASE_DIR,
    )
    if r.returncode != 0:
        return None

    return r.stdout.strip().split('\n') if r.stdout.strip() else []


def _needs_cross_platform_rebuild(changed_files):
    """判断变更是否需要重建对端产物。

    需要重建：核心模块、配置文件、打包脚本等影响构建产物的文件。
    不需要重建：tests/、docs/、.md、脚本、CI 配置等。
    """
    if changed_files is None:
        return True  # 无法判断，保守起见重建

    # 需要重建的文件（改了影响构建产物内容）
    SHARED_BUILD_FILES = {
        'gui_main.py', 'bossmaster.py', 'filtering.py', 'llm_eval.py', 'ai_adapter.py',
        'job_ai_parser.py', 'job_config_diagnostics.py', 'storage.py', 'doc_parser.py', 'security.py', 'constants.py',
        'paths.py', 'icons.py', 'updater.py', 'selectors.json',
        'job_config.json', 'api_config.json', 'ui_config.json', 'requirements.txt',
        'build.py',  # 打包脚本本身的变化影响产物内容
    }

    # 需要重建的目录（改了影响构建产物内容）
    REBUILD_PREFIXES = (
        'pyinstaller-hooks/',  # 自定义 hook 控制模块收集
    )

    # 不需要重建的目录/文件前缀
    SKIP_PREFIXES = (
        'tests/', 'scripts/', 'docs/', '.github/', '.claude/',
        'AGENTS.md', 'CHANGELOG.md', 'CLAUDE.md', 'DEPLOYMENT.md',
        'GUI', 'PACKAGING.md', 'README', 'TODO.md',
        'latest.json', '.gitignore',
    )

    for f in changed_files:
        if not f.strip():
            continue

        # 明确需要重建
        if f in SHARED_BUILD_FILES:
            return True

        # 需要重建的目录
        if any(f.startswith(p) for p in REBUILD_PREFIXES):
            return True

        # 明确不需要重建（前缀匹配 + .md 后缀兜底）
        if f.endswith('.md') or any(f.startswith(p) for p in SKIP_PREFIXES):
            continue

        # 未知文件：跳过（不保守触发 CI，避免每次发布都白等 CI）
        print(f"  [信息] 未分类文件: {f}，跳过（不影响构建产物）")

    return False


def _local_build_outputs():
    """Return files that prove the current platform artifact exists."""
    if IS_MAC:
        return [
            DIST_DIR / "BOSS_ResumeFilter.app",
            DIST_DIR / "BOSS_ResumeFilter_mac.zip",
            DIST_DIR / "BOSS_ResumeFilter.dmg",
        ]
    return [DIST_DIR / "BOSS_ResumeFilter.exe"]


def _build_input_files():
    """Return files that affect the PyInstaller output.

    Auto-scan all .py files in project root (excluding test files),
    plus config files. This avoids missing newly added modules.
    """
    # 扫描项目根目录的所有 .py 文件
    py_files = []
    for f in BASE_DIR.glob("*.py"):
        # 排除测试文件和打包脚本本身
        if f.name.startswith("test_") or f.name.endswith("_test.py") or f.name == "build.py":
            continue
        py_files.append(f.name)

    # 配置文件（影响打包内容）
    config_files = [
        "requirements.txt", "job_config.json",
        "api_config.json", "selectors.json", "ui_config.json", "CHANGELOG.md",
    ]

    # PyInstaller hooks（控制模块收集范围）
    hook_files = []
    hooks_dir = BASE_DIR / "pyinstaller-hooks"
    if hooks_dir.exists():
        hook_files = [str(f.relative_to(BASE_DIR)) for f in hooks_dir.glob("*.py")]

    all_files = py_files + config_files + hook_files
    return [BASE_DIR / f for f in all_files if (BASE_DIR / f).exists()]


def _build_fingerprint(pyinstaller_cmd):
    """Build a stable fingerprint for deciding whether PyInstaller must run."""
    file_hashes = {}
    for path in _build_input_files():
        rel = path.relative_to(BASE_DIR).as_posix()
        file_hashes[rel] = _sha256_file(path)

    payload = {
        "version": BUILD_FINGERPRINT_VERSION,
        "platform": sys.platform,
        "python": sys.version,
        "is_mac": IS_MAC,
        "is_win": IS_WIN,
        "pyinstaller_cmd": [str(x) for x in pyinstaller_cmd],
        "files": file_hashes,
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()


def _read_build_state():
    try:
        return json.loads(BUILD_STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _write_build_state(fingerprint):
    data = {
        "fingerprint": fingerprint,
        "platform": sys.platform,
        "outputs": [str(p.relative_to(BASE_DIR)) for p in _local_build_outputs()],
        "updated_at": datetime.now().isoformat(timespec="seconds"),
    }
    BUILD_STATE_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _needs_local_rebuild(pyinstaller_cmd):
    """Return (needs_rebuild, reason) using artifact existence plus build fingerprint."""
    missing = [p for p in _local_build_outputs() if not p.exists()]
    if missing:
        return True, f"缺少产物: {', '.join(p.name for p in missing)}"

    current = _build_fingerprint(pyinstaller_cmd)
    previous = _read_build_state().get("fingerprint")
    if previous != current:
        return True, "构建指纹变化"

    return False, "构建指纹未变化"


def _get_github_release_assets(tag):
    """获取 GitHub Release 上的产物列表。返回按文件名索引的资产元数据。"""
    data = _github_release_view_json(tag, "assets")
    if data is None:
        return {}
    assets = data.get("assets", [])
    return {a["name"]: a for a in assets}


def _delete_github_release_assets(tag, asset_names):
    """从 GitHub Release 删除指定产物。返回成功删除的文件名列表。"""
    deleted = []
    for name in asset_names:
        success = False
        for attempt in range(3):
            r = subprocess.run(
                ["gh", "release", "delete-asset", tag, name, "-y"],
                capture_output=True, text=True, cwd=BASE_DIR,
            )
            if r.returncode == 0:
                print(f"  [OK] 已删除旧产物: {name}")
                deleted.append(name)
                success = True
                break
            if attempt < 2:
                print(f"  [重试] 删除 {name} 失败 (attempt {attempt+1}/3), 5s 后重试...")
                time.sleep(5)
        if not success:
            print(f"  [警告] 删除 {name} 失败: {r.stderr}")
    return deleted


def _verify_assets_deleted(tag, asset_names):
    """验证产物已从 GitHub Release 删除。

    GitHub API 删除是同步操作，delete-asset 返回 0 即已删除。
    只查询一次确认，不再轮询等待。
    """
    current = _get_github_release_assets(tag)
    still_present = [n for n in asset_names if n in current]
    if not still_present:
        return True
    print(f"  [警告] 删除可能未生效，仍存在: {', '.join(still_present)}")
    return False


def _trigger_cross_platform_ci(tag, old_tag_commit=None):
    """新版本发布或同提交断点续跑时，按需触发对端 CI 重建。

    Windows 发布 → 删旧 DMG/ZIP → CI 自动构建 macOS
    macOS 发布 → 删旧 EXE → CI 自动构建 Windows

    流程：
    1. 判断变更是否需要重建对端（按需重建，避免无效构建）
    2. 检查对端产物是否存在，不存在则跳过
    3. 记录旧产物的 size 和 updatedAt，用于 CI 后对比
    4. 删除对端旧产物
    5. 验证删除已生效（轮询，防止 GitHub API 延迟导致 CI 看到旧产物）
    6. 触发 CI workflow
    7. 返回 (need_ci, old_assets_info)

    Returns:
        tuple: (need_ci: bool, old_assets_info: dict)
            need_ci: 是否需要等待 CI 构建对端产物
            old_assets_info: 旧产物的 {文件名: {size, updatedAt}}，用于后续校验
    """
    if IS_MAC:
        opposite_assets = ["BOSS_ResumeFilter.exe"]
    else:
        opposite_assets = ["BOSS_ResumeFilter_mac.zip", "BOSS_ResumeFilter.dmg"]

    # 1. 判断是否需要重建对端（按需重建）
    changed_files = _get_changed_files_since_tag(old_tag_commit)
    if not _needs_cross_platform_rebuild(changed_files):
        print("  [跳过] 变更仅影响当前平台或纯文档，无需重建对端产物")
        if changed_files:
            print(f"  变更文件: {', '.join(changed_files[:5])}{'...' if len(changed_files) > 5 else ''}")
        return False, {}

    print("  [信息] 检测到跨平台变更，需要重建对端产物")
    if changed_files:
        print(f"  变更文件: {', '.join(changed_files[:5])}{'...' if len(changed_files) > 5 else ''}")

    # 2. 检查对端旧产物并删除（首次发布时 present 为空，跳过删除但仍触发 CI）
    current_assets = _get_github_release_assets(tag)
    present = {n: current_assets[n] for n in opposite_assets if n in current_assets}
    old_assets_info = dict(present) if present else {}

    if present:
        print(f"  发现对端旧产物: {', '.join(present.keys())}")

        # 3. 删除对端旧产物
        deleted = _delete_github_release_assets(tag, list(present.keys()))
        if not deleted:
            print("  [跳过] 删除失败，不触发 CI")
            return False, {}

        # 4. 验证删除已生效（防止 GitHub API 延迟导致 CI 看到旧产物而跳过构建）
        if not _verify_assets_deleted(tag, deleted):
            print("  [警告] 对端产物删除未生效，CI 可能跳过构建")
    else:
        print("  [信息] 对端产物不存在（首次发布），直接触发 CI")

    # 5. 触发 CI workflow
    r = subprocess.run(
        ["gh", "workflow", "run", "release.yml", "--ref", tag],
        capture_output=True, text=True, cwd=BASE_DIR,
    )
    if r.returncode == 0:
        print(f"  [OK] 已触发 CI 构建对端产物 ({tag})")
    else:
        print(f"  [警告] CI 触发失败: {r.stderr.strip()}")
        print("  可手动执行: gh workflow run release.yml --ref " + tag)

    return True, old_assets_info


def _gitee_session(retries=3):
    """创建带自动重试的 requests Session，用于 Gitee API 调用。

    自动重试 5xx/429/连接错误（3 次），减少 Gitee 服务不稳定导致的失败。
    """
    session = requests.Session()
    retry = Retry(
        total=retries,
        backoff_factor=1,        # 1s, 2s, 4s
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET", "POST", "PATCH", "DELETE", "HEAD"],
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


def _gitee_ping(token, timeout=15):
    """预检 Gitee API 连通性，失败后立即重试 3 次。"""
    session = _gitee_session(retries=0)
    for attempt in range(4):
        try:
            resp = session.head(
                "https://gitee.com/api/v5/user",
                params={"access_token": token},
                timeout=timeout,
            )
            if resp.status_code < 500:
                return True
        except requests.exceptions.RequestException:
            pass
        if attempt < 3:
            delay = 2 * (attempt + 1)
            print(f"  [Gitee] API 预检失败，{delay}s 后重试 ({attempt+1}/3)")
            time.sleep(delay)
    return False


def _gitee_find_or_create_release(api_base, token, tag, release_title, release_notes):
    """查找或创建 Gitee Release，返回 (release_id, existing_assets)。

    existing_assets: {文件名: {"id": 附件ID, "size": 文件大小}}，用于增量上传。
    内置重试：每次 API 调用最多重试 3 次（间隔 5s），应对 Gitee 服务不稳定。
    """
    session = _gitee_session(retries=0)
    max_attempts = 4

    def _find_release_once():
        try:
            current = session.get(
                f"{api_base}/releases",
                params={"access_token": token, "page": 1, "per_page": 100},
                timeout=30,
            )
            current.raise_for_status()
            return next(
                (item for item in current.json() if item.get("tag_name") == tag),
                None,
            )
        except requests.exceptions.RequestException:
            return None

    def _retry_request(method, url, *, accepted=None, **kwargs):
        """带手动重试的请求包装器，应对非 5xx 的瞬态失败。"""
        for attempt in range(max_attempts):
            try:
                resp = getattr(session, method)(url, **kwargs)
                resp.raise_for_status()
                return resp
            except requests.exceptions.RequestException as e:
                if accepted is not None and accepted():
                    return None
                status = getattr(getattr(e, "response", None), "status_code", None)
                if status is not None and 400 <= status < 500 and status not in {
                    408,
                    409,
                    429,
                }:
                    raise
                if attempt < max_attempts - 1:
                    delay = 2 * (attempt + 1)
                    detail = f"HTTP {status}" if status else type(e).__name__
                    print(f"  [Gitee] API 请求失败 ({detail})，{delay}s 后重试 ({attempt+1}/3)")
                    time.sleep(delay)
                else:
                    raise

    resp = _retry_request("get",
        f"{api_base}/releases",
        params={"access_token": token, "page": 1, "per_page": 100}, timeout=30)
    release = next((r for r in resp.json() if r.get("tag_name") == tag), None)

    if release:
        release_id = release["id"]
        existing_assets = _gitee_fetch_assets(api_base, token, release_id, _retry_request)
        # 标题或正文有变化时同步更新（以 CHANGELOG 为准）
        new_name = release_title or tag
        # 如果 release_notes 为空，保持 Gitee 原有 body 不变（避免传空字符串导致 400 错误）
        new_body = release_notes if release_notes else release.get("body", "")
        old_name = release.get("name", "")
        old_body = release.get("body", "")
        if old_name != new_name or old_body != new_body:
            _retry_request("patch",
                f"{api_base}/releases/{release_id}",
                accepted=lambda: (
                    (current := _find_release_once()) is not None
                    and current.get("name", "") == new_name
                    and current.get("body", "") == new_body
                ),
                params={"access_token": token},
                json={
                    "tag_name": tag,
                    "name": new_name,
                    "body": new_body,
                },
                timeout=30)
            if old_name != new_name:
                print(f"  [OK] Gitee Release 标题已更新: {new_name}")
            if old_body != new_body:
                print(f"  [OK] Gitee Release 正文已同步 ({len(new_body)} 字符)")
        return release_id, existing_assets

    recovered_release = {}

    def _created_after_lost_response():
        current = _find_release_once()
        if current is None:
            return False
        recovered_release.update(current)
        return True

    resp = _retry_request("post",
        f"{api_base}/releases",
        accepted=_created_after_lost_response,
        params={"access_token": token},
        json={
            "tag_name": tag,
            "name": release_title or tag,
            "body": release_notes or "",
            "target_commitish": "master",
        },
        timeout=30)
    print(f"  [OK] Gitee Release 已创建: {tag}")
    created = recovered_release or resp.json()
    return created["id"], {}


def _gitee_fetch_assets(api_base, token, release_id, retry_fn=None):
    """获取 Release 附件列表，返回 {文件名: {"id": int, "size": int, "created_at": str}}。

    retry_fn: 可选的重试请求函数。未提供时使用普通 requests。
    """
    url = f"{api_base}/releases/{release_id}/attach_files"
    params = {"access_token": token}
    if retry_fn:
        resp = retry_fn("get", url, params=params, timeout=30)
    else:
        session = _gitee_session()
        resp = session.get(url, params=params, timeout=30)
        resp.raise_for_status()
    return {
        a["name"]: {
            "id": a["id"],
            "size": a.get("size", 0),
            "created_at": a.get("created_at", ""),
        }
        for a in resp.json()
    }


def _gitee_request_once(session, method, url, **kwargs):
    """Issue one Gitee request for mutation postcondition verification."""
    response = getattr(session, method)(url, **kwargs)
    response.raise_for_status()
    return response


def _format_size(size_bytes):
    """格式化字节数，供发布日志展示。"""
    size = float(size_bytes or 0)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} B"
        size /= 1024


def _gitee_fetch_releases(api_base, token):
    """获取 Gitee Release 列表，返回所有分页结果。"""
    session = _gitee_session()
    releases = []
    page = 1
    while True:
        resp = session.get(
            f"{api_base}/releases",
            params={"access_token": token, "page": page, "per_page": 100},
            timeout=30,
        )
        resp.raise_for_status()
        batch = resp.json()
        if not batch:
            break
        releases.extend(batch)
        if len(batch) < 100:
            break
        page += 1
    return releases


def _gitee_clean_old_assets(keep_version, apply=False, *, token=None, skip_ping=False):
    """清理 Gitee 旧版本附件，仅保留 keep_version 对应 Release 的产物。"""
    token = token or os.environ.get("GITEE_TOKEN")
    if not token:
        print("  [跳过] Gitee Release: 未设置 GITEE_TOKEN 环境变量")
        return False

    if keep_version.startswith("v"):
        keep_version = keep_version[1:]
    keep_tag = f"v{keep_version}"

    if not skip_ping and not _gitee_ping(token):
        print("  [失败] Gitee API 不可达")
        return False

    owner = "yaoyouzhong"
    repo = "boss-resume-filter"
    api_base = f"https://gitee.com/api/v5/repos/{owner}/{repo}"

    releases = _gitee_fetch_releases(api_base, token)
    candidates = []
    for release in releases:
        tag = release.get("tag_name") or ""
        release_id = release.get("id")
        if not release_id or tag == keep_tag:
            continue
        candidates.append((tag, release_id))

    def _fetch_candidate(item):
        tag, release_id = item
        return tag, release_id, _gitee_fetch_assets(api_base, token, release_id)

    fetched = []
    if len(candidates) <= 1:
        fetched = [_fetch_candidate(item) for item in candidates]
    else:
        workers = min(8, len(candidates))
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {
                executor.submit(_fetch_candidate, item): item
                for item in candidates
            }
            for future in as_completed(futures):
                fetched.append(future.result())

    stale = []
    for tag, release_id, assets in fetched:
        for name, asset in assets.items():
            stale.append({
                "tag": tag,
                "release_id": release_id,
                "name": name,
                "id": asset["id"],
                "size": int(asset.get("size") or 0),
            })

    total_size = sum(item["size"] for item in stale)
    if not stale:
        print(f"  [OK] Gitee 旧版本无附件需要清理，仅保留 {keep_tag}")
        return True

    action = "将删除" if apply else "预览"
    print(f"\n>>> Gitee 旧版本附件清理{action}（保留 {keep_tag}）")
    print(f"  旧附件数量: {len(stale)}")
    print(f"  可释放空间: {_format_size(total_size)}")
    for item in stale:
        print(f"  - {item['tag']}: {item['name']} ({_format_size(item['size'])})")

    if not apply:
        print("\n  [预览] 未执行删除。确认后运行：")
        print(f"  python build.py --gitee-clean-old-assets {keep_version} --apply")
        return True

    for item in stale:
        _gitee_delete_asset(api_base, token, item["release_id"], item["id"],
                            f"{item['tag']}/{item['name']}")

    print(f"\n  [OK] Gitee 旧版本附件已清理，保留 {keep_tag} 的产物")
    return True


def _gitee_upload_single(filepath, api_base, token, release_id, max_retries=3):
    """上传单个文件到 Gitee Release，带重试。返回 (文件名, 响应JSON)。

    只重试 5xx / 连接错误 / 超时。4xx 客户端错误直接抛出，不做无效重试。
    """
    session = _gitee_session(retries=0)
    total_attempts = max_retries + 1
    for attempt in range(total_attempts):
        try:
            with open(filepath, "rb") as fh:
                resp = session.post(
                    f"{api_base}/releases/{release_id}/attach_files",
                    files={"file": (filepath.name, fh)},
                    params={"access_token": token},
                    timeout=TRANSFER_TIMEOUT_SECONDS,
                )
            if 400 <= resp.status_code < 500:
                detail = resp.text.strip()
                if len(detail) > 300:
                    detail = detail[:300] + "..."
                raise requests.exceptions.HTTPError(
                    f"{resp.status_code} Client Error: {detail}",
                    response=resp,
                )
            resp.raise_for_status()
            return filepath.name, resp.json()
        except requests.exceptions.RequestException as e:
            try:
                current = _gitee_fetch_assets(
                    api_base,
                    token,
                    release_id,
                    lambda method, url, **kwargs: _gitee_request_once(
                        session, method, url, **kwargs
                    ),
                )
            except requests.exceptions.RequestException:
                current = {}
            remote = current.get(filepath.name)
            if remote and int(remote.get("size") or 0) == filepath.stat().st_size:
                print(f"  [OK] Gitee 已收到 {filepath.name}，忽略丢失的上传响应")
                return filepath.name, remote
            status = getattr(getattr(e, "response", None), "status_code", None)
            # 参数、认证等确定性 4xx 不重试；408/429 属于瞬时失败。
            detail = (
                f"{type(e).__name__}: "
                f"{release_retry.redact_sensitive_text(e, (token,))}"
            )
            sanitized = requests.exceptions.RequestException(
                detail,
                response=getattr(e, "response", None),
            )
            if status is not None and 400 <= status < 500 and status not in {408, 429}:
                raise sanitized from None
            if attempt < total_attempts - 1:
                delay = 2 * (attempt + 1)
                print(f"  [Gitee] {filepath.name} 上传失败 ({detail})，{delay}s 后重试 ({attempt+1}/{max_retries})")
                time.sleep(delay)
            else:
                raise sanitized from None


def _gitee_asset_url(owner, repo, tag, filename):
    """构造 Gitee Release 下载链接。"""
    return f"https://gitee.com/{owner}/{repo}/releases/download/{tag}/{filename}"


def _gitee_delete_asset(api_base, token, release_id, asset_id, filename):
    """删除 Gitee Release 上的旧附件，带 3 次重试。"""
    session = _gitee_session(retries=0)
    for attempt in range(4):
        try:
            resp = session.delete(
                f"{api_base}/releases/{release_id}/attach_files/{asset_id}",
                params={"access_token": token},
                timeout=30,
            )
            resp.raise_for_status()
            print(f"  删除旧附件: {filename}")
            return
        except requests.exceptions.RequestException as e:
            try:
                current = _gitee_fetch_assets(
                    api_base,
                    token,
                    release_id,
                    lambda method, url, **kwargs: _gitee_request_once(
                        session, method, url, **kwargs
                    ),
                )
            except requests.exceptions.RequestException:
                current = {filename: {}}
            if filename not in current:
                print(f"  删除旧附件: {filename}（远端已不存在）")
                return
            status = getattr(getattr(e, "response", None), "status_code", None)
            detail = (
                f"{type(e).__name__}: "
                f"{release_retry.redact_sensitive_text(e, (token,))}"
            )
            sanitized = requests.exceptions.RequestException(
                detail,
                response=getattr(e, "response", None),
            )
            if status is not None and 400 <= status < 500 and status not in {408, 429}:
                raise sanitized from None
            if attempt < 3:
                delay = 2 * (attempt + 1)
                print(f"  [Gitee] 删除附件 {filename} 失败 ({detail})，{delay}s 后重试 ({attempt+1}/3)")
                time.sleep(delay)
            else:
                raise sanitized from None


def _gitee_get_release_cache(
    version,
    release_title,
    release_notes,
    *,
    token=None,
    skip_ping=False,
):
    """获取 Gitee Release 缓存信息（API 连通性、release_id、existing assets）。

    返回 dict 或 None（失败时）。调用方应将此 dict 传递给后续的上传函数以避免重复 API 调用。
    """
    token = token or os.environ.get("GITEE_TOKEN")
    if not token:
        print("  [跳过] Gitee Release: 未设置 GITEE_TOKEN 环境变量")
        return None

    # 连通性预检：避免批量操作时才发现 API 不可达
    if not skip_ping and not _gitee_ping(token):
        print(f"\n{'!'*60}")
        print(f"  [!!]  Gitee API 不可达，跳过 Gitee 上传")
        print(f"  手动补传: python build.py --gitee-upload {version}")
        print(f"{'!'*60}\n")
        return None

    owner = "yaoyouzhong"
    repo = "boss-resume-filter"
    tag = f"v{version}"
    api_base = f"https://gitee.com/api/v5/repos/{owner}/{repo}"

    try:
        release_id, existing = _gitee_find_or_create_release(
            api_base, token, tag, release_title, release_notes)

        return {
            'token': token,
            'owner': owner,
            'repo': repo,
            'tag': tag,
            'api_base': api_base,
            'release_id': release_id,
            'existing': existing,
        }
    except requests.exceptions.RequestException as e:
        print(f"\n{'!'*60}")
        status = getattr(getattr(e, "response", None), "status_code", None)
        detail = f"HTTP {status}" if status else type(e).__name__
        print(f"  [!!]  Gitee Release 获取失败: {detail}")
        print(f"  手动补传: python build.py --gitee-upload {version}")
        print(f"{'!'*60}\n")
        return None


def _gitee_upload_artifacts(version, release_title, release_notes, artifact_paths,
                             release_cache=None, large_workers=1,
                             fail_fast=False):
    """Upload an explicit artifact set to Gitee with idempotent checks.

    This platform-neutral entrypoint is used by the local finalization phase,
    after it downloads the Windows and macOS GitHub Draft artifacts into one
    workspace. It returns canonical ``downloads_cn`` entries for all
    successfully verified files, or ``None`` when any upload fails.
    """
    # 如果没有传入缓存，则获取缓存
    if release_cache is None:
        release_cache = _gitee_get_release_cache(version, release_title, release_notes)
        if release_cache is None:
            return None

    token = release_cache['token']
    owner = release_cache['owner']
    repo = release_cache['repo']
    tag = release_cache['tag']
    api_base = release_cache['api_base']
    release_id = release_cache['release_id']
    existing = release_cache['existing']

    files = [Path(path) for path in artifact_paths]
    files = [path for path in files if path.exists() and path.is_file()]

    try:
        downloads_cn = {}
        failed = []

        def _process_and_upload(files, label):
            """增量上传：文件内容一致则跳过，其余删旧后上传。"""
            if not files:
                return
            to_upload = []
            for f in files:
                if f.name in existing:
                    remote = existing[f.name]
                    same, reason = _gitee_asset_matches_local(f, remote, owner, repo, tag, token=token)
                    if same:
                        url = _gitee_asset_url(owner, repo, tag, f.name)
                        downloads_cn[_downloads_cn_key(f.name)] = url
                        print(f"  [跳过] Gitee 已有且一致: {f.name} ({reason})")
                        continue
                    print(f"  [更新] Gitee {reason}: {f.name}")
                    _gitee_delete_asset(api_base, token, release_id, remote["id"], f.name)
                to_upload.append(f)
            if not to_upload:
                return

            def _upload_success(f, result):
                name, asset = result
                url = asset.get("browser_download_url",
                                _gitee_asset_url(owner, repo, tag, name))
                downloads_cn[_downloads_cn_key(name)] = url
                print(f"  [OK] Gitee 已上传: {name}")

            def _upload_failure(f, error):
                detail = release_retry.redact_sensitive_text(error, (token,))
                print(f"  [失败] Gitee 上传失败: {f.name} ({detail})")
                failed.append(f.name)
                if fail_fast:
                    if isinstance(error, requests.exceptions.RequestException):
                        raise requests.exceptions.RequestException(
                            detail,
                            response=getattr(error, "response", None),
                        ) from None
                    raise error

            if fail_fast:
                for f in to_upload:
                    print(f"  {label} 串行: {f.name}")
                    try:
                        result = _gitee_upload_single(
                            f, api_base, token, release_id
                        )
                        _upload_success(f, result)
                    except Exception as error:
                        _upload_failure(f, error)
            else:
                _run_transfer_batch(
                    to_upload,
                    label,
                    lambda f: _gitee_upload_single(f, api_base, token, release_id),
                    _upload_success,
                    _upload_failure,
                    large_workers=max(1, int(large_workers)),
                )

        _process_and_upload(files, "上传发布产物到 Gitee")

        if failed:
            print(f"\n{'!'*60}")
            print(f"  [!!]  Gitee 上传部分失败: {', '.join(failed)}")
            print(f"  手动补传: python build.py --gitee-upload {version}")
            print(f"{'!'*60}\n")
            return None

        return downloads_cn if downloads_cn else None

    except requests.exceptions.RequestException as e:
        print(f"\n{'!'*60}")
        detail = release_retry.redact_sensitive_text(e, (token,))
        print(f"  [!!]  Gitee Release 整体失败: {detail}")
        print(f"  手动补传: python build.py --gitee-upload {version}")
        print(f"{'!'*60}\n")
        return None


def _gitee_upload_local(version, release_title, release_notes, release_cache=None):
    """Upload the artifacts built on the current local platform to Gitee."""
    if IS_MAC:
        artifacts = [
            DIST_DIR / "BOSS_ResumeFilter_mac.zip",
            DIST_DIR / "BOSS_ResumeFilter.dmg",
        ]
        workers = 2
    else:
        artifacts = [DIST_DIR / "BOSS_ResumeFilter.exe"]
        workers = 1
    return _gitee_upload_artifacts(
        version,
        release_title,
        release_notes,
        artifacts,
        release_cache=release_cache,
        large_workers=workers,
    )


def _downloads_cn_key(filename):
    """文件名 → downloads_cn 字典 key。"""
    if filename.endswith(".exe"):
        return "windows"
    if filename.endswith("_mac.zip"):
        return "macos"
    if filename.endswith(".dmg"):
        return "macos_dmg"
    return filename


def _download_from_github_release(tag, asset_name, dest_dir):
    """从 GitHub Release 下载单个产物。返回下载后的 Path。"""
    owner = "yaoyouzhong"
    repo = "boss-resume-filter"
    url = (
        f"https://api.github.com/repos/{owner}/{repo}/releases/assets/"
    )
    # 使用 gh CLI 下载更可靠（自动认证）
    dest = Path(dest_dir) / asset_name

    # 首次失败后立即重试 3 次。
    for attempt in range(4):
        try:
            r = subprocess.run(
                ["gh", "release", "download", tag, "-p", asset_name, "-D", str(dest_dir), "--clobber"],
                capture_output=True, text=True, cwd=BASE_DIR, timeout=TRANSFER_TIMEOUT_SECONDS,
            )
            if r.returncode == 0:
                return dest
            error = r.stderr.strip()
        except subprocess.TimeoutExpired as e:
            error = f"下载超时 ({TRANSFER_TIMEOUT_SECONDS}s)"
        if attempt < 3:
            delay = 2 * (attempt + 1)
            print(f"  [重试] 下载 {asset_name} 失败: {error} (attempt {attempt+1}/3), {delay}s 后重试...")
            time.sleep(delay)

    raise RuntimeError(f"gh download 失败（已重试 3 次）：{error}")


def _wait_for_github_release_assets(tag, asset_names, max_wait=600, poll_interval=30):
    """Wait until all requested assets appear in GitHub Release."""
    elapsed = 0
    while elapsed <= max_wait:
        current = _get_github_release_assets(tag)
        missing = [name for name in asset_names if name not in current]
        if not missing:
            return True
        if elapsed >= max_wait:
            break
        print(f"  等待 GitHub Release 产物... {elapsed}s / {max_wait}s，缺少: {', '.join(missing)}")
        time.sleep(poll_interval)
        elapsed += poll_interval
    print(f"  [警告] 等待 GitHub Release 产物超时，缺少: {', '.join(missing)}")
    return False


def _collect_github_release_asset_metadata(version, existing_metadata=None):
    """Download missing update artifacts from GitHub Release and compute full metadata."""
    tag = f"v{version}"
    metadata = dict(existing_metadata or _release_asset_metadata())
    required_assets = {
        "windows": "BOSS_ResumeFilter.exe",
        "macos": "BOSS_ResumeFilter_mac.zip",
        "macos_dmg": "BOSS_ResumeFilter.dmg",
    }

    missing = [name for key, name in required_assets.items() if key not in metadata]
    if not missing:
        return metadata

    remote_assets = _get_github_release_assets(tag)
    if remote_assets:
        remote_metadata = _release_asset_metadata_from_remote_assets(remote_assets.values())
        metadata.update({
            key: value
            for key, value in remote_metadata.items()
            if key not in metadata
        })
        missing = [name for key, name in required_assets.items() if key not in metadata]
        if not missing:
            print("  [OK] 已从 GitHub Release 读取对端产物完整性元数据")
            return metadata

    if not _wait_for_github_release_assets(tag, missing):
        return metadata

    remote_assets = _get_github_release_assets(tag)
    if remote_assets:
        remote_metadata = _release_asset_metadata_from_remote_assets(remote_assets.values())
        metadata.update({
            key: value
            for key, value in remote_metadata.items()
            if key not in metadata
        })
        missing = [name for key, name in required_assets.items() if key not in metadata]
        if not missing:
            print("  [OK] 已从 GitHub Release 读取对端产物完整性元数据")
            return metadata

    download_dir = DIST_DIR / "_metadata_download"
    download_dir.mkdir(exist_ok=True)
    downloaded = []
    try:
        for name in missing:
            path = _download_from_github_release(tag, name, download_dir)
            downloaded.append(path)
        metadata.update(_release_asset_metadata(downloaded))
        return metadata
    finally:
        shutil.rmtree(download_dir, ignore_errors=True)


def _sync_gitee_from_github(version, release_title, release_notes, need_wait=False, release_cache=None):
    """从 GitHub Release 下载对端产物并上传到 Gitee Release。

    当 need_wait=True 时，先轮询等待 CI 构建完成（对端产物出现在 GitHub Release）。
    小文件优先并发传输，大文件随后串行传输，避免网络波动时大文件互相抢带宽。
    release_cache: 可选的缓存信息（来自 _gitee_get_release_cache），避免重复 API 调用。
    返回 downloads_cn 字典，失败返回 None。
    """
    # 如果没有传入缓存，则获取缓存
    if release_cache is None:
        release_cache = _gitee_get_release_cache(version, release_title, release_notes)
        if release_cache is None:
            return None

    token = release_cache['token']
    owner = release_cache['owner']
    repo = release_cache['repo']
    tag = release_cache['tag']
    api_base = release_cache['api_base']
    release_id = release_cache['release_id']
    existing = release_cache['existing']
    try:
        existing = _gitee_fetch_assets(api_base, token, release_id)
        release_cache['existing'] = existing
    except requests.exceptions.RequestException as e:
        detail = release_retry.redact_sensitive_text(e, (token,))
        print(f"  [Gitee] 刷新附件列表失败，继续使用缓存: {detail}")

    # 对端产物列表
    if IS_MAC:
        opposite_assets = ["BOSS_ResumeFilter.exe"]
    else:
        opposite_assets = ["BOSS_ResumeFilter_mac.zip", "BOSS_ResumeFilter.dmg"]
    downloads_cn = {}

    if need_wait:
        print(f"  等待 CI 构建对端产物（{', '.join(opposite_assets)}）...")
        # 优化：前 60s 每 10s 轮询，之后每 30s 轮询
        max_wait = 600
        elapsed = 0
        poll_interval = 10
        while elapsed < max_wait:
            try:
                r = subprocess.run(
                    ["gh", "release", "view", tag, "--json", "assets"],
                    capture_output=True, text=True, cwd=BASE_DIR,
                )
                if r.returncode == 0:
                    gh_assets = {a["name"] for a in json.loads(r.stdout).get("assets", [])}
                    if all(name in gh_assets for name in opposite_assets):
                        print(f"  [OK] 对端产物已就绪（{elapsed}s）")
                        break
                    missing = [n for n in opposite_assets if n not in gh_assets]
                    print(f"  等待中... {elapsed}s / {max_wait}s，缺少: {', '.join(missing)}")
                else:
                    print(f"  等待中... {elapsed}s / {max_wait}s（GitHub Release 查询失败）")
            except Exception:
                pass
            time.sleep(poll_interval)
            elapsed += poll_interval
            # 60s 后切换到 30s 间隔
            if elapsed >= 60:
                poll_interval = 30
        else:
            print(f"\n{'!'*60}")
            print(f"  [!!]  等待超时 ({max_wait}s)，CI 可能未完成")
            print(f"  手动同步: python build.py --gitee-upload {version}")
            print(f"{'!'*60}\n")
            return None

    github_assets = _get_github_release_assets(tag)
    to_download = []
    for name in opposite_assets:
        if name in existing and name in github_assets:
            if _gitee_asset_can_reuse_github_metadata(name, existing[name], github_assets[name]):
                url = _gitee_asset_url(owner, repo, tag, name)
                downloads_cn[_downloads_cn_key(name)] = url
                print(f"  [跳过] Gitee 对端产物已可复用: {name} (GitHub digest 与 latest.json 一致)")
                continue
        to_download.append(name)

    if not to_download:
        print("  [跳过] 无需下载对端产物")
        return downloads_cn if downloads_cn else None

    # 同步对端产物：小文件可并发，大文件按 opposite_assets 顺序串行。
    # Windows 同步 macOS 产物时，ZIP 必须先于 DMG，保证自动更新包优先可用。
    download_dir = DIST_DIR / "_gh_download"
    download_dir.mkdir(exist_ok=True)

    failed = []

    def _sync_one_asset(name):
        """下载一个产物并上传到 Gitee。"""
        # 1. 从 GitHub 下载
        path = _download_from_github_release(tag, name, download_dir)
        print(f"  [OK] 已下载: {name}")

        # 2. 检查 Gitee 是否已有且一致
        if name in existing:
            remote = existing[name]
            same, reason = _gitee_asset_matches_local(path, remote, owner, repo, tag, token=token)
            if same:
                url = _gitee_asset_url(owner, repo, tag, name)
                print(f"  [跳过] Gitee 已有且一致: {name} ({reason})")
                return name, url

        # 3. 删除旧附件（如有）
        if name in existing:
            remote = existing[name]
            print(f"  [更新] Gitee {name}")
            _gitee_delete_asset(api_base, token, release_id, remote["id"], name)

        # 4. 上传到 Gitee
        _name, asset = _gitee_upload_single(path, api_base, token, release_id)
        url = asset.get("browser_download_url",
                        _gitee_asset_url(owner, repo, tag, name))
        print(f"  [OK] Gitee 已上传: {name}")
        return name, url

    try:
        def _sync_success(name, result):
            asset_name, url = result
            downloads_cn[_downloads_cn_key(asset_name)] = url

        def _sync_failure(name, error):
            detail = release_retry.redact_sensitive_text(error, (token,))
            print(f"  [失败] {name}: {detail}")
            failed.append(name)

        _run_transfer_batch(
            to_download,
            "同步 GitHub 到 Gitee",
            _sync_one_asset,
            _sync_success,
            _sync_failure,
            remote_assets=github_assets,
            large_workers=2 if len(to_download) > 1 else 1,
        )

        if failed:
            print(f"\n{'!'*60}")
            print(f"  [!!]  Gitee 同步部分失败: {', '.join(failed)}")
            print(f"  手动补传: python build.py --gitee-upload {version}")
            print(f"{'!'*60}\n")

        return downloads_cn if downloads_cn else None

    except requests.exceptions.RequestException as e:
        print(f"\n{'!'*60}")
        detail = release_retry.redact_sensitive_text(e, (token,))
        print(f"  [!!]  Gitee Release 同步失败: {detail}")
        print(f"  手动补传: python build.py --gitee-upload {version}")
        print(f"{'!'*60}\n")
        return None
    finally:
        shutil.rmtree(download_dir, ignore_errors=True)


# ---------------------------------------------------------------------------
#  主流程
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="BOSS 简历筛选器 - 打包及发布脚本")
    parser.add_argument("--check", action="store_true",
                        help="仅执行发布前检查，不打包、不提交、不推送")
    parser.add_argument("--user-audit", action="store_true",
                        help="用户视角发布审计，不打包、不提交、不推送")
    parser.add_argument("--release", action="store_true",
                        help="仅与 --ci 配合供 GitHub Actions 构建使用")
    parser.add_argument("--version", type=str, default=None, metavar="X.Y",
                        help="自动更新 gui_main.py 中的 __version__")
    parser.add_argument("--ci", action="store_true",
                        help="CI 模式：跳过虚拟环境切换和 git 操作，用于 GitHub Actions")
    parser.add_argument("--gitee-upload", type=str, default=None, metavar="X.Y.Z",
                        help="手动补传产物到 Gitee Release（需要 GITEE_TOKEN）")
    parser.add_argument("--verify-gitee-integrity", type=str, default=None, metavar="X.Y.Z",
                        help="回下载 Gitee Release 产物并逐文件校验 SHA256")
    parser.add_argument("--verify-release", type=str, default=None, metavar="X.Y.Z",
                        help="只读核验双远端分支/tag、Release 附件和 latest.json")
    parser.add_argument("--gitee-clean-old-assets", type=str, default=None, metavar="X.Y.Z",
                        help="清理 Gitee 旧版本附件，仅保留指定版本产物；默认只预览")
    parser.add_argument("--github-upload", type=str, default=None, metavar="X.Y.Z",
                        help="手动补传产物到 GitHub Release")
    parser.add_argument("--apply", action="store_true",
                        help="配合 --gitee-clean-old-assets 执行真实删除")
    parser.add_argument("--auto", action="store_true",
                        help="全自动模式：跳过推送确认，用于 Claude Code 等非交互环境")
    parser.add_argument("--force-build", action="store_true",
                        help="忽略构建指纹缓存，强制重新执行 PyInstaller")
    parser.add_argument("--no-gitee", action="store_true",
                        help="发布时跳过 Gitee Release 上传和 downloads_cn 更新")
    parser.add_argument("--no-ci-sync", action="store_true",
                        help="发布时跳过跨平台 CI 重建和对端产物同步")
    parser.add_argument("--sync-release-notes", action="store_true",
                        help="修正 CHANGELOG 后同步 GitHub + Gitee Release 说明，不重新打包")
    parser.add_argument("--strict-changelog", action="store_true",
                        help="将 CHANGELOG 启发式覆盖、README 逐条镜像和 latest.json 同步检查作为硬门禁")
    parser.add_argument("--prepared-sha", default="", help=argparse.SUPPRESS)
    args = parser.parse_args()

    if args.release and not args.ci:
        requested_version = (args.version or _read_version()).removeprefix("v")
        _validate_version_format(requested_version)
        print("本地 build.py --release 已停用，避免绕过正式发布编排。")
        print("请在 PR 合并后先预览并确认最终发布内容：")
        print(
            "  python scripts/release_dispatch.py "
            f"--version {requested_version}"
        )
        sys.exit(2)

    version_changed = False

    # ---- 版本号更新（在打包之前） ----
    if args.version:
        _validate_version_format(args.version)  # 校验新版本号格式
        old = _read_version()
        if args.version != old:
            _write_version(args.version)
            version_changed = True
        else:
            print(f"  [跳过] __version__ 已经是 \"{args.version}\"\n")

    # 读取当前版本号并校验格式
    current_version = _read_version()
    _validate_version_format(current_version)

    if args.user_audit:
        issues = audit_user_facing_release(BASE_DIR)
        print(summarize_release_user_audit(issues))
        if any(issue.severity == "error" for issue in issues):
            sys.exit(1)
        return

    if not args.ci:
        run_in_venv()

    if args.sync_release_notes:
        _sync_release_notes()
        return

    if args.check:
        _preflight_checks(require_clean=True, strict_changelog=args.strict_changelog)
        return

    if args.verify_release:
        version = args.verify_release.lstrip("v")
        _validate_version_format(version)
        if not _verify_release_remote_state(version):
            sys.exit(1)
        return

    if args.gitee_clean_old_assets:
        version = args.gitee_clean_old_assets
        _gitee_clean_old_assets(version, apply=args.apply)
        return

    if args.verify_gitee_integrity:
        version = args.verify_gitee_integrity.lstrip("v")
        _validate_version_format(version)
        release_title, release_notes = _extract_changelog_release(version)
        release_cache = _gitee_get_release_cache(version, release_title, release_notes)
        if release_cache is None:
            sys.exit(1)
        print(f"\n>>> 严格校验 Gitee Release v{version}")
        if not _verify_release_assets_complete(
            f"v{version}",
            release_cache=release_cache,
            verify_gitee_sha256=True,
        ):
            sys.exit(1)
        return

    if args.gitee_upload:
        version = args.gitee_upload
        # 移除可能的 'v' 前缀，避免 tag 变成 'vv2.9'
        if version.startswith('v'):
            version = version[1:]
        tag = f"v{version}"
        print(f"\n>>> 手动上传产物到 Gitee Release {tag}")

        # 从 GitHub Release 读取 release notes
        release_title = tag
        release_notes = ""
        try:
            r = subprocess.run(
                ["gh", "release", "view", tag, "--json", "name,body"],
                capture_output=True, text=True, encoding='utf-8', cwd=BASE_DIR,
            )
            if r.returncode == 0:
                data = json.loads(r.stdout)
                release_title = data.get("name", tag)
                release_notes = data.get("body", "")
        except Exception:
            pass

        # 上传本地已有的产物
        downloads_cn = _gitee_upload_local(version, release_title, release_notes)

        # 从 GitHub 下载对端产物并上传到 Gitee（不等待，CI 应已完成）
        gitee_sync = _sync_gitee_from_github(version, release_title, release_notes, need_wait=False)
        if gitee_sync:
            downloads_cn = downloads_cn or {}
            downloads_cn.update(gitee_sync)

        if downloads_cn:
            asset_metadata = _collect_github_release_asset_metadata(
                version, existing_metadata=_release_asset_metadata())
            changed = update_latest_json(
                version,
                release_notes,
                downloads_cn,
                asset_metadata=asset_metadata,
                require_complete_assets=True,
            )
            if changed:
                print(f"\n  [OK] downloads_cn 已更新，请手动提交推送：")
                print(f"  git add latest.json && git commit -m \"chore: 更新 Gitee 下载链接\" && git push")
            else:
                print(f"\n  [跳过] latest.json 无变化，无需提交")
        else:
            print(f"\n  [失败] Gitee 上传未成功")
        return

    if args.github_upload:
        version = args.github_upload
        # 移除可能的 'v' 前缀，避免 tag 变成 'vv2.9'
        if version.startswith('v'):
            version = version[1:]
        tag = f"v{version}"
        print(f"\n>>> 手动上传产物到 GitHub Release {tag}")

        # 检查 Release 是否存在
        r = subprocess.run(["gh", "release", "view", tag], capture_output=True, cwd=BASE_DIR)
        if r.returncode != 0:
            print(f"[错误] GitHub Release {tag} 不存在，请先运行正式发布本机驱动器")
            sys.exit(1)

        # 从 GitHub Release 读取 release notes
        release_title = tag
        release_notes = ""
        try:
            r = subprocess.run(
                ["gh", "release", "view", tag, "--json", "name,body"],
                capture_output=True, text=True, encoding='utf-8', cwd=BASE_DIR,
            )
            if r.returncode == 0:
                data = json.loads(r.stdout)
                release_title = data.get("name", tag)
                release_notes = data.get("body", "")
        except Exception:
            pass

        # 上传缺失的产物
        if IS_MAC:
            dmg = DIST_DIR / "BOSS_ResumeFilter.dmg"
            mac_zip = DIST_DIR / "BOSS_ResumeFilter_mac.zip"
            artifacts = [(dmg, "DMG"), (mac_zip, "Mac-ZIP")]
        else:
            exe = DIST_DIR / "BOSS_ResumeFilter.exe"
            artifacts = [(exe, "EXE")]

        print(f"  准备上传 {len(artifacts)} 个文件")

        # 检查已存在的资源
        existing = subprocess.run(["gh", "release", "view", tag, "--json", "assets"],
                                  capture_output=True, text=True, cwd=BASE_DIR)
        remote_assets = {}
        if existing.returncode == 0 and existing.stdout.strip():
            assets = json.loads(existing.stdout).get("assets", [])
            remote_assets = {a["name"]: a for a in assets}

        to_upload = []
        for f, label in artifacts:
            if f.exists():
                remote_asset = remote_assets.get(f.name)
                if remote_asset:
                    same, reason = _github_asset_matches_local(tag, f, remote_asset)
                    if same:
                        print(f"  [跳过] {f.name} 已存在且一致")
                        continue
                    print(f"  [更新] {f.name}: {reason}")
                else:
                    print(f"  [上传] {f.name}")

                to_upload.append(f)
            else:
                print(f"  [跳过] {f.name} 不存在")

        uploaded_names = []

        def _github_upload_success(f, name):
            uploaded_names.append(name)
            print(f"  [OK] {name}")

        def _github_upload_failure(f, error):
            print(f"  [失败] {f.name} 上传失败: {error}")
            raise error

        _run_transfer_batch(
            to_upload,
            "上传 GitHub Release",
            lambda f: _upload_github_release_asset(tag, f),
            _github_upload_success,
            _github_upload_failure,
        )

        if uploaded_names:
            print(f"\n  [OK] 已上传 {len(uploaded_names)} 个文件到 GitHub Release {tag}")
            print(f"  查看 Release: https://github.com/yaoyouzhong/boss-resume-filter/releases/tag/{tag}")
        else:
            print(f"\n  [跳过] 没有需要上传的文件")
        return

    print("""
╔══════════════════════════════════════════════════════════════╗
║         BOSS 简历筛选器 - 自动打包脚本 (v2)                   ║
╚══════════════════════════════════════════════════════════════╝
""")

    # 步骤名称定义（release 模式 5 步，纯打包模式 2 步）
    if args.release:
        step_names = ['发布前检查',
                      'Git 提交 + 打标签 + 推送 + 触发 CI',
                      'PyInstaller 打包',
                      'Release 发布',
                      'latest.json 更新']
    else:
        step_names = ['发布前检查', 'PyInstaller 打包']
    progress = ReleaseProgress(_read_version(), step_names)

    # ---- 步骤 1：发布前检查 ----
    progress.start_step(0)
    if args.prepared_sha:
        if not (args.ci and args.release):
            print("错误: --prepared-sha 必须与 --ci --release 同时使用")
            sys.exit(1)
        _validate_prepared_ci_build(args.prepared_sha)
    else:
        _preflight_checks(
            require_clean=not version_changed,
            strict_changelog=args.strict_changelog,
        )
    progress.end_step()

    tk_args, pyinstaller_env = _pyinstaller_tk_args()
    if tk_args:
        progress.sub('将 Anaconda Tcl/Tk 运行库加入 PyInstaller')

    # CI 模式使用当前 Python，否则使用虚拟环境
    python_exe = sys.executable if args.ci else str(VENV_PYTHON)

    cmd = [
        python_exe, "-m", "PyInstaller",
    ]

    # macOS: --onedir --windowed (生成 .app bundle)
    # Windows: --onefile --noconsole (生成单文件 .exe)
    if IS_MAC:
        cmd += ["--onedir", "--windowed", "--osx-bundle-identifier", "com.boss.resume-filter"]
    else:
        cmd += [
            "--onefile",
            "--noconsole",
            "--runtime-tmpdir",
            r"%LOCALAPPDATA%",
        ]

    def _generate_clean_api_config():
        """生成干净的默认 api_config.json，不含个人配置（打包时临时生成）"""
        clean_config = {
            "api_provider": "deepseek",
            "base_url": "https://api.deepseek.com",
            "model": "deepseek-chat",
            "saved_models": [],
            "providers": {},
            "fetched_models": {}
        }
        temp_path = BASE_DIR / "build" / "api_config.json"
        temp_path.parent.mkdir(parents=True, exist_ok=True)
        temp_path.write_text(json.dumps(clean_config, indent=2, ensure_ascii=False), encoding="utf-8")
        return temp_path

    clean_api_config = _generate_clean_api_config()

    cmd += [
        '--name', 'BOSS_ResumeFilter',
        '--additional-hooks-dir', str(BASE_DIR / 'pyinstaller-hooks'),
        '--add-data', f'{BASE_DIR / "job_config.json"}{SEP}.',
        '--add-data', f'{clean_api_config}{SEP}.',
        '--add-data', f'{BASE_DIR / "selectors.json"}{SEP}.',
        '--add-data', f'{BASE_DIR / "ui_config.json"}{SEP}.',
        '--add-data', f'{BASE_DIR / "CHANGELOG.md"}{SEP}.',
    ]

    # babel locale-data: 只收集项目实际使用的语言（排除全部 1086 个 locale 文件，按需添加）
    # 虚拟环境可能是 pack_venv（本地）或 .venv-ci（CI），路径含 python3.X 层，用 glob 兼容
    import glob as _glob
    _venv_roots = [VENV_DIR, BASE_DIR / ".venv-ci"]
    _venv_site_globs = [
        str(root / "lib" / "python*" / "site-packages" / "babel" / "locale-data")  # macOS/Linux
        for root in _venv_roots
    ] + [
        str(root / "Lib" / "site-packages" / "babel" / "locale-data")  # Windows
        for root in _venv_roots
    ]
    babel_locale_dir = None
    for pattern in _venv_site_globs:
        matches = _glob.glob(pattern)
        if matches:
            babel_locale_dir = Path(matches[0])
            break

    babel_locales = [
        "root.dat",
        "en.dat", "en_US.dat",
        "zh.dat", "zh_Hans.dat", "zh_Hans_CN.dat",
        "zh_Hant.dat", "zh_Hant_TW.dat", "zh_Hant_HK.dat",
    ]
    added_count = 0
    if babel_locale_dir and babel_locale_dir.exists():
        for loc in babel_locales:
            loc_file = babel_locale_dir / loc
            if loc_file.exists():
                cmd += ['--add-data', f'{loc_file}{SEP}babel/locale-data']
                added_count += 1
        print(f"  [OK] babel locale-data: 已添加 {added_count}/{len(babel_locales)} 个文件 (来源: {babel_locale_dir})")
    else:
        print(f"  [警告] babel locale-data 未找到！tkcalendar 日期选择器将无法正常启动")
        print(f"         搜索路径: {', '.join(_venv_site_globs)}")
        print(f"         请确认 babel 已安装且虚拟环境目录正确")

    cmd += [
        '--hidden-import=tkinter',
        '--hidden-import=tkinter.ttk',
        '--hidden-import=tkinter.font',
        '--hidden-import=tkinter.filedialog',
        '--hidden-import=tkinter.messagebox',
        '--hidden-import=tkcalendar',
        '--hidden-import=pdfminer',
        '--hidden-import=education_certificate',
        '--hidden-import=striprtf',
        '--hidden-import=babel',
        '--hidden-import=babel.numbers',
        '--hidden-import=babel.dates',
        *tk_args,
        '--hidden-import=PIL',
        '--hidden-import=PIL.Image',
        '--hidden-import=PIL.ImageDraw',
        '--hidden-import=PIL.ImageTk',
        '--hidden-import=PIL.ImageColor',
        '--hidden-import=PIL.ImageFont',
        '--exclude-module=PIL._avif',
        '--exclude-module=PIL._webp',
        '--exclude-module=PyQt5',
        '--exclude-module=PySide6',
        '--exclude-module=torch',
        '--exclude-module=botocore',
        '--exclude-module=boto3',
        '--exclude-module=matplotlib',
        '--exclude-module=scipy',
        # openpyxl 会可选导入 numpy 以支持 numpy 标量；本应用导出普通 Python 值，强制排除避免打进 OpenBLAS。
        '--exclude-module=numpy',
        '--exclude-module=numpy.libs',
        '--exclude-module=IPython',
        '--exclude-module=pytest',
        '--exclude-module=notebook',
        # lxml.objectify 当前没有运行期入口；lxml.html 由 DrissionPage 顶层导入，不能排除。
        '--exclude-module=lxml.objectify',
        str(BASE_DIR / "gui_main.py")
    ]

    # ---- Release 模式提前：Git commit + tag + push + 触发 CI ----
    # 在 PyInstaller 打包前完成 Git 操作并触发 macOS CI，使 CI 与本地打包并行
    ci_triggered = False
    old_tag_commit = None
    if args.release and not args.ci:
        progress.start_step(1)

        # 任何已有 tag 都必须与当前提交一致；存在待提交变更时不能复用旧 tag。
        has_pending_changes, _status_text = _git_status()
        _assert_release_tag_reusable(
            current_version,
            will_create_commit=has_pending_changes,
        )

        # 2a: 提交变更（如 --version 导致的版本号修改）
        allowed = ["gui_main.py"] if args.version else []
        _git_commit(current_version, allowed_paths=allowed)

        # 2b: 记录旧 tag commit（用于 CI diff），然后打 tag
        old_tag_commit = _git_tag(current_version)

        # 2c: 推送 master + tag
        _git_push(current_version, auto=args.auto)

        # 2d: 提前触发 macOS CI（与后续 PyInstaller 并行）
        if not args.no_ci_sync:
            tag = f"v{current_version}"
            changed_files = _get_changed_files_since_tag(old_tag_commit)
            if _needs_cross_platform_rebuild(changed_files):
                # 删除对端旧产物（首次发布时不存在则跳过）
                current_assets = _get_github_release_assets(tag)
                if IS_MAC:
                    opposite = ["BOSS_ResumeFilter.exe"]
                else:
                    opposite = ["BOSS_ResumeFilter_mac.zip", "BOSS_ResumeFilter.dmg"]
                present = {n: current_assets[n] for n in opposite if n in current_assets}
                if present:
                    progress.sub(f'删除对端旧产物: {", ".join(present.keys())}')
                    _delete_github_release_assets(tag, list(present.keys()))

                # 触发对端 CI（--ref tag + -f platform=<对端>，避免 CI 重复构建本地平台）
                opposite_platform = "windows" if IS_MAC else "macos"
                r = subprocess.run(
                    ["gh", "workflow", "run", "release.yml",
                     "--ref", tag, "-f", f"platform={opposite_platform}"],
                    capture_output=True, text=True, cwd=BASE_DIR)
                if r.returncode == 0:
                    progress.sub(f'[OK] {opposite_platform} CI 已触发（与本地打包并行）')
                    ci_triggered = True
                else:
                    progress.sub(f'[警告] macOS CI 触发失败: {r.stderr.strip()}')
            else:
                if changed_files:
                    progress.sub(f'[跳过] 变更不影响对端平台')

        progress.end_step()

    # ---- 步骤 3（Release）或步骤 2（非 Release）：PyInstaller 打包 ----
    # Release 模式下 CI 已在后台运行，本地打包与 CI 并行
    build_step_idx = 2 if (args.release and not args.ci) else 1
    # 检查是否需要重新打包（避免无意义的重复构建）
    if args.force_build:
        needs_rebuild, rebuild_reason = True, "--force-build"
    else:
        needs_rebuild, rebuild_reason = _needs_local_rebuild(cmd)
    if not needs_rebuild:
        progress.skip_step(build_step_idx, reason=rebuild_reason)
        print(f"  [跳过] PyInstaller 打包（{rebuild_reason}）")
    else:
        print(f"  [信息] 需要重新打包：{rebuild_reason}")
        clean_dist()
        progress.start_step(build_step_idx)
        os.chdir(BASE_DIR)

        # 使用 Popen 实时显示进度
        start_time = time.time()
        process = subprocess.Popen(
            cmd,
            env=pyinstaller_env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1
        )

        # 实时读取输出并显示进度
        output_lines = []
        # braille spinner 在 GBK 终端无法编码，用 ASCII 回退
        try:
            '⠋'.encode(sys.stdout.encoding or 'utf-8')
            spinner = ['⠋', '⠙', '⠹', '⠸', '⠼', '⠴', '⠦', '⠧', '⠇', '⠏']
        except (UnicodeEncodeError, LookupError):
            spinner = ['|', '/', '-', '\\', '|', '/', '-', '\\', '|', '/']
        spinner_idx = 0
        last_update = 0

        for line in process.stdout:
            output_lines.append(line)
            elapsed = time.time() - start_time

            # 每 2 秒更新一次进度显示（PyInstaller spinner 在进度表下方输出）
            if elapsed - last_update >= 2:
                if 'INFO:' in line:
                    if 'Building' in line:
                        step = '构建中'
                    elif 'Analyzing' in line:
                        step = '分析依赖'
                    elif 'Processing' in line:
                        step = '处理模块'
                    else:
                        step = '打包中'
                else:
                    step = '打包中'

                print(f"\r  {spinner[spinner_idx % len(spinner)]} {step}... {elapsed:.0f}s", end='', flush=True)
                spinner_idx += 1
                last_update = elapsed

        process.wait()
        elapsed = time.time() - start_time

        if process.returncode != 0:
            progress.fail_step(f'打包失败（{elapsed:.0f}s）')
            print(f"\n[错误] 打包失败（耗时 {elapsed:.0f}s）")
            print(">>> PyInstaller 输出:")
            print(''.join(output_lines[-50:]))
            sys.exit(1)

        # 清掉 spinner 行
        print(f"\r{' ' * 60}\r", end='')

    print("  更新辅助文件...")
    for file in ["README.md", "job_config.json", "ui_config.json"]:
        src = BASE_DIR / file
        dst = DIST_DIR / file
        if src.exists():
            shutil.copy2(src, dst)
            print(f"    + {file}")
        else:
            print(f"    ! {file} (源文件缺失)")

    # macOS: 创建 ZIP 和 DMG 分发包
    if IS_MAC and needs_rebuild:
        _create_mac_zip()
        _create_mac_dmg()

    version, artifact_path, size_mb = _check_version_consistency()
    if needs_rebuild:
        _write_build_state(_build_fingerprint(cmd))
    release_title = release_notes = None
    if args.release:
        release_title, release_notes = _extract_changelog_release(version)

    if needs_rebuild:
        progress.end_step()

    # ---- Release 模式：Release 发布 + latest.json ----
    if args.release and not args.ci:

        # ---- 步骤 4：Release 发布（GitHub + Gitee，内部并行化） ----
        progress.start_step(3)
        downloads_cn = _gh_release(
            version,
            release_title,
            release_notes,
            progress,
            enable_gitee=not args.no_gitee,
            enable_ci_sync=not args.no_ci_sync,
            old_tag_commit=old_tag_commit,
            ci_already_triggered=ci_triggered,
        )
        # 清理 Gitee 旧版本产物（如果启用 Gitee）
        if not args.no_gitee:
            _gitee_clean_old_assets(version, apply=True)
        progress.end_step()

        # ---- 步骤 5：latest.json 更新 ----
        progress.start_step(4)
        _ensure_current_platform_github_assets_match_local(
            f"v{version}",
            [artifact_path] if artifact_path.exists() else [],
            report=progress.sub,
        )
        asset_metadata = _collect_github_release_asset_metadata(
            version, existing_metadata=_release_asset_metadata())
        latest_changed = update_latest_json(
            version,
            release_notes,
            downloads_cn,
            asset_metadata=asset_metadata,
            require_complete_assets=True,
        )
        if latest_changed:
            subprocess.run(["git", "add", "latest.json"], cwd=BASE_DIR, check=True)
            subprocess.run(["git", "commit", "-m", "chore: 更新自动更新清单"],
                           cwd=BASE_DIR, check=True)
            # 推送 latest.json，带重试
            for attempt in range(3):
                try:
                    subprocess.run(["git", "push", "origin", "master"], cwd=BASE_DIR, check=True)
                    progress.sub('latest.json 已自动提交并推送')
                    break
                except subprocess.CalledProcessError as e:
                    if attempt < 2:
                        progress.sub(f'[重试] 推送 latest.json 失败 (attempt {attempt+1}/3), 5s 后重试...')
                        time.sleep(5)
                    else:
                        raise
        else:
            progress.sub('latest.json 无变化，跳过提交和推送')
        progress.end_step()

        # 最终汇总表
        progress.render_final(artifact_path, size_mb)
    elif args.release and args.ci:
        print(f"\n{'='*60}")
        print(f"  CI 打包完成：v{version}")
        print(f"  {artifact_path} ({size_mb:.1f} MB)")
        print(f"  GitHub Actions 将自动上传到 Release")
        print(f"{'='*60}\n")
    else:
        print("\n  正式发布：先合并发布准备 PR，再从本机启动正式发布驱动器")
        print(f"  授权文本：正式发布 v{version}\n")


if __name__ == "__main__":
    main()

