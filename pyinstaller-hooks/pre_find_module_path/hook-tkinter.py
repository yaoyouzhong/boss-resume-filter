"""Keep tkinter discoverable when Conda's Tcl probe fails despite complete files."""
from __future__ import annotations

import os
import sys
from pathlib import Path

from PyInstaller import log as logging
from PyInstaller.utils.hooks import tcl_tk

logger = logging.getLogger(__name__)


def pre_find_module_path(hook_api) -> None:
    """Restore the stdlib search path only for a complete Conda Tk layout."""
    if os.environ.get("EDUCATION_TOOL_TK_FALLBACK") != "1":
        # Preserve PyInstaller's built-in hook semantics for every other build.
        if not tcl_tk.tcltk_info.available:
            logger.warning(
                "tkinter installation is broken. It will be excluded from the application"
            )
            hook_api.search_dirs = []
        return
    if tcl_tk.tcltk_info.available:
        return

    base_prefix = Path(sys.base_prefix).resolve()
    required = (
        base_prefix / "Lib" / "tkinter" / "__init__.py",
        base_prefix / "DLLs" / "_tkinter.pyd",
        base_prefix / "Library" / "lib" / "tcl8.6" / "init.tcl",
        base_prefix / "Library" / "lib" / "tk8.6" / "tk.tcl",
        base_prefix / "Library" / "bin" / "tcl86t.dll",
        base_prefix / "Library" / "bin" / "tk86t.dll",
    )
    if all(path.is_file() for path in required):
        hook_api.search_dirs = [str(base_prefix / "Lib")]
