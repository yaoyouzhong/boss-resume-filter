"""Global Delete must respect lazy pages and the exact focused widget."""
from unittest.mock import Mock, patch

from gui_app_shell import PageIndex
from gui_main import BossFilterGUI


def _host():
    gui = BossFilterGUI.__new__(BossFilterGUI)
    gui.root = Mock()
    gui._remove_selected_candidates = Mock()
    return gui


def test_delete_before_results_page_exists_is_a_quiet_noop():
    gui = _host()
    for page in (PageIndex.HOME, PageIndex.EDUCATION, PageIndex.RESULTS):
        gui.current_page_index = page
        with patch('gui_main.logger.warning') as warning:
            gui._shortcut_delete_selected()
        warning.assert_not_called()
    gui.root.focus_get.assert_not_called()
    gui._remove_selected_candidates.assert_not_called()


def test_delete_on_another_page_does_not_use_a_previously_created_table():
    gui = _host()
    gui.current_page_index = PageIndex.EDUCATION
    gui.result_tree = Mock()
    gui.root.focus_get.return_value = gui.result_tree
    gui._shortcut_delete_selected()
    gui._remove_selected_candidates.assert_not_called()


def test_delete_in_input_or_popdown_does_not_remove_candidates():
    gui = _host()
    gui.current_page_index = PageIndex.RESULTS
    gui.result_tree = Mock()
    gui.root.focus_get.return_value = Mock()
    gui._shortcut_delete_selected()
    gui.root.focus_get.side_effect = KeyError('popdown')
    with patch('gui_main.logger.warning') as warning:
        gui._shortcut_delete_selected()
    warning.assert_not_called()
    gui._remove_selected_candidates.assert_not_called()


def test_delete_removes_only_selected_rows_in_the_focused_result_table():
    gui = _host()
    gui.current_page_index = PageIndex.RESULTS
    gui.result_tree = Mock()
    gui.root.focus_get.return_value = gui.result_tree
    gui.result_tree.selection.return_value = ()
    gui._shortcut_delete_selected()
    gui._remove_selected_candidates.assert_not_called()
    gui.result_tree.selection.return_value = ('candidate-1',)
    gui._shortcut_delete_selected()
    gui._remove_selected_candidates.assert_called_once_with()


def test_delete_ignores_destroyed_result_table():
    gui = _host()
    gui.current_page_index = PageIndex.RESULTS
    gui.result_tree = Mock()
    gui.result_tree.winfo_exists.return_value = False
    gui._shortcut_delete_selected()
    gui.root.focus_get.assert_not_called()
    gui._remove_selected_candidates.assert_not_called()
