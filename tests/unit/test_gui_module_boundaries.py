import ast
from pathlib import Path

import changelog_renderer
import gui_dialogs
import gui_main
import ui_windowing
import updater


ROOT = Path(__file__).resolve().parents[2]


def _top_level_imports(module_name: str) -> set[str]:
    """Return only imports executed while the module itself is imported."""
    tree = ast.parse((ROOT / f"{module_name}.py").read_text(encoding="utf-8"))
    imported = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    return imported


def test_gui_modules_share_leaf_helpers_without_duplicate_implementations():
    assert gui_main._place_window_centered is ui_windowing.place_window_centered
    assert gui_main._get_windows_monitor_area is ui_windowing.get_windows_monitor_area
    assert gui_dialogs.place_window_centered is ui_windowing.place_window_centered
    assert updater.place_window_centered is ui_windowing.place_window_centered
    assert gui_dialogs.render_changelog_text is changelog_renderer.render_changelog_text
    assert updater.render_changelog_text is changelog_renderer.render_changelog_text


def test_dialog_and_updater_imports_do_not_recreate_gui_main_cycle():
    assert "gui_main" not in _top_level_imports("gui_dialogs")
    assert "gui_main" not in _top_level_imports("updater")
    assert "gui_dialogs" not in _top_level_imports("updater")
