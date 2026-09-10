from education_presenter import screenshot_session_summary


def test_screenshot_counts_only_current_attempt():
    items = {
        "old": {"screenshot_status": "已存在"},
        "saved": {"screenshot_attempt_status": "已保存"},
        "error": {"screenshot_attempt_status": "截图失败"},
        "waiting": {"screenshot_attempt_status": "待结果页"},
    }
    assert screenshot_session_summary(items) == "已保存 1｜未保存 2｜保存失败 1"
    for item in items.values():
        item.pop("screenshot_attempt_status", None)
    assert screenshot_session_summary(items) == "已保存 0｜未保存 4｜保存失败 0"
