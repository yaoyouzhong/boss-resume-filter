import shutil
import os
import sys
from pathlib import Path


def get_base_dir() -> Path:
    """
    获取程序基础目录（处理 PyInstaller 打包后的路径）。

    - 源码运行：返回脚本所在目录
    - Windows EXE：返回 EXE 所在目录
    - macOS .app：返回 .app 的父目录（因为 sys.executable 指向 .app/Contents/MacOS/）
    """
    if getattr(sys, 'frozen', False):
        exe_dir = Path(sys.executable).parent.resolve()
        if sys.platform == 'darwin' and exe_dir.name == 'MacOS':
            base = exe_dir.parent.parent.parent
            # 防御性检查：确保目录存在且可写
            if base.exists() and os.access(str(base), os.W_OK):
                return base
            # fallback: 使用用户目录
            fallback = Path.home() / ".boss-resume-filter"
            fallback.mkdir(parents=True, exist_ok=True)
            return fallback
        return exe_dir
    return Path(__file__).parent.resolve()


def ensure_config_files(base_dir: Path) -> None:
    """
    确保配置文件存在。首次运行时（如 macOS DMG 安装后），
    从 PyInstaller 嵌入的 _MEIPASS 复制默认配置到可写位置。

    Args:
        base_dir: 程序基础目录
    """
    if not getattr(sys, 'frozen', False):
        return

    if not hasattr(sys, '_MEIPASS'):
        return

    meipass = Path(sys._MEIPASS)
    config_files = ["job_config.json", "selectors.json", "api_config.json", "ui_config.json"]

    for fname in config_files:
        target = base_dir / fname
        if not target.exists():
            src = meipass / fname
            if src.exists():
                try:
                    shutil.copy2(str(src), str(target))
                except OSError as e:
                    print(f"无法复制配置文件 {fname}：{e}。如果从 DMG 运行，请将应用拖到 Applications 文件夹后重试。")


def get_api_config_path(base_dir: Path | None = None, *, for_write: bool = False) -> Path:
    """Return the runtime API config path.

    In source mode, user edits go to ignored api_config.local.json so the
    tracked api_config.json can stay a release/default template. Frozen builds
    keep using api_config.json next to the executable.
    """
    root = base_dir or BASE_DIR
    default_path = root / "api_config.json"
    local_path = root / "api_config.local.json"
    if getattr(sys, 'frozen', False):
        return default_path
    if for_write or local_path.exists():
        return local_path
    return default_path


# 便捷常量
BASE_DIR = get_base_dir()
SELECTORS_PATH = BASE_DIR / "selectors.json"
CONFIG_PATH = BASE_DIR / "job_config.json"
CANDIDATES_PATH = BASE_DIR / "candidates_all.json"
CANDIDATES_XLSX_PATH = BASE_DIR / "candidates_all.xlsx"
CONTACT_QUEUE_PATH = BASE_DIR / "contact_queue.json"
