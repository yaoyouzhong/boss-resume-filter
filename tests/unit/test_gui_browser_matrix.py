"""Executable contracts for the GUI/display/browser acceptance matrix."""
import ast
import json
from pathlib import Path
from unittest.mock import patch

import bossmaster
from constants import EMPTY_RECOMMEND_MARKS
from gui_main import BossFilterGUI
from ui_layout import result_display_columns


ROOT = Path(__file__).resolve().parents[2]
MATRIX_PATH = ROOT / "tests" / "gui_browser_acceptance_matrix.json"


def _matrix():
    return json.loads(MATRIX_PATH.read_text(encoding="utf-8"))


def _display_case(case_id):
    return next(
        case for case in _matrix()["display_cases"]
        if case["id"] == case_id
    )


def _assert_display_case(case_id):
    case = _display_case(case_id)
    columns = result_display_columns(
        case["result_tree_width"],
        maximized=case["window_mode"] == "maximized",
    )
    assert len(columns) == case["expected_column_count"]
    return columns


def test_result_policy_windows_1080p_windowed_compact():
    assert len(_assert_display_case("windows-1080p-windowed-compact")) == 8


def test_result_policy_windows_1080p_maximized_includes_school_company():
    columns = _assert_display_case(
        "windows-1080p-maximized-all-fields"
    )
    assert columns[-2:] == ("school", "company")


def test_result_policy_windows_4k_windowed_does_not_force_wide_columns():
    columns = _assert_display_case("windows-4k-windowed-readable")
    assert len(columns) == 11
    assert "school" not in columns
    assert "company" not in columns


def test_result_policy_windows_4k_maximized_includes_all_fields():
    columns = _assert_display_case("windows-4k-maximized-all-fields")
    assert len(columns) == 13
    assert columns[-2:] == ("school", "company")


def test_result_policy_macos_retina_windowed_keeps_core_columns_readable():
    columns = _assert_display_case("macos-retina-windowed-core")
    assert len(columns) == 8


class _TopPage:
    def __init__(self, url, page_text="", *, disconnected=False):
        self.url = url
        self.page_text = page_text
        self.disconnected = disconnected

    def run_js(self, script):
        if self.disconnected:
            raise RuntimeError("context lost")
        if script == "return 1":
            return 1
        if "slice(0, 800)" in script:
            return self.page_text
        raise AssertionError(f"unexpected top-page script: {script[:40]}")


class _TargetPage:
    def __init__(self, state):
        self.state = state

    def run_js(self, _script):
        return dict(self.state)


def _readiness(top_page, state=None):
    gui = BossFilterGUI.__new__(BossFilterGUI)
    gui.browser_page = top_page
    target = _TargetPage(state or {})
    with patch.object(bossmaster, "get_iframe", return_value=target):
        return gui._get_run_page_readiness()


def test_browser_matrix_disconnected():
    gui = BossFilterGUI.__new__(BossFilterGUI)
    gui.browser_page = None

    ready, reason = gui._get_run_page_readiness()

    assert ready is False
    assert "未连接" in reason


def test_browser_matrix_context_lost():
    ready, reason = _readiness(
        _TopPage(
            "https://www.zhipin.com/web/chat/recommend",
            disconnected=True,
        )
    )

    assert ready is False
    assert "连接已丢失" in reason


def test_browser_matrix_login_page():
    ready, reason = _readiness(
        _TopPage(
            "https://www.zhipin.com/web/user/login",
            "扫码登录",
        )
    )

    assert ready is False
    assert "登录页" in reason


def test_browser_matrix_non_recommend_page():
    ready, reason = _readiness(
        _TopPage("https://www.zhipin.com/web/geek/chat", "沟通")
    )

    assert ready is False
    assert "推荐牛人页面" in reason


def test_browser_matrix_recommend_page_loading():
    ready, reason = _readiness(
        _TopPage(
            "https://www.zhipin.com/web/chat/recommend",
            "推荐牛人",
        ),
        {
            "readyState": "loading",
            "href": "https://www.zhipin.com/web/frame/recommend",
            "hasCards": False,
            "text": "推荐牛人",
        },
    )

    assert ready is False
    assert "正在加载" in reason


def test_browser_matrix_no_published_job():
    ready, reason = _readiness(
        _TopPage(
            "https://www.zhipin.com/web/chat/recommend",
            "推荐牛人",
        ),
        {
            "readyState": "complete",
            "href": "https://www.zhipin.com/web/frame/recommend",
            "hasCards": False,
            "text": "您需要先发布职位，才能查看推荐牛人",
        },
    )

    assert ready is False
    assert "没有可用的已发布职位" in reason


def test_browser_matrix_empty_candidate_page_is_valid():
    ready, reason = _readiness(
        _TopPage(
            "https://www.zhipin.com/web/chat/recommend",
            "推荐牛人",
        ),
        {
            "readyState": "complete",
            "href": "https://www.zhipin.com/web/frame/recommend",
            "hasCards": False,
            "text": EMPTY_RECOMMEND_MARKS[0],
        },
    )

    assert ready is True
    assert reason == ""


def test_browser_matrix_recommend_page_ready():
    ready, reason = _readiness(
        _TopPage(
            "https://www.zhipin.com/web/chat/recommend",
            "推荐牛人",
        ),
        {
            "readyState": "complete",
            "href": (
                "https://www.zhipin.com/web/frame/recommend"
                "?jobid=encrypted-job"
            ),
            "hasCards": True,
            "text": "推荐牛人",
        },
    )

    assert ready is True
    assert reason == ""


def test_acceptance_matrix_has_unique_required_coverage():
    matrix = _matrix()
    assert matrix["schema_version"] == 1
    all_cases = [
        *matrix["display_cases"],
        *matrix["browser_cases"],
        *matrix["manual_cases"],
    ]
    ids = [case["id"] for case in all_cases]
    assert len(ids) == len(set(ids))
    assert {
        case["id"] for case in matrix["browser_cases"]
    } >= {
        "browser-disconnected",
        "browser-context-lost",
        "boss-login-page",
        "non-recommend-page",
        "recommend-loading",
        "recommend-no-published-job",
        "recommend-no-candidates",
        "recommend-ready",
        "boss-cooldown-active",
    }
    manual_ids = {case["id"] for case in matrix["manual_cases"]}
    assert "windows-1080p-maximized-visual" in manual_ids
    assert "windows-4k-windowed-visual" in manual_ids
    assert "macos-retina-all-pages" in manual_ids
    assert "real-chrome-login-recommend-empty-ready" in manual_ids


def test_acceptance_matrix_automation_references_existing_unit_tests():
    discovered = set()
    for path in (ROOT / "tests" / "unit").glob("test_*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        discovered.update(
            node.name
            for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name.startswith("test_")
        )
    matrix = _matrix()
    references = {
        case["automated_test"]
        for case in [
            *matrix["display_cases"],
            *matrix["browser_cases"],
        ]
    }

    assert references <= discovered
