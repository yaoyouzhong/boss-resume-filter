from greeting_failure import diagnose_greeting_failure, format_greeting_failure_message


def test_greeting_failure_detects_limit_as_terminal():
    diagnosis = diagnose_greeting_failure("沟通次数已达上限")

    assert diagnosis.category == "limit"
    assert diagnosis.terminal is True
    assert "明天再试" in diagnosis.action


def test_greeting_failure_detects_wrong_page_when_page_required():
    diagnosis = diagnose_greeting_failure("按钮未找到", page_required=True)

    assert diagnosis.category == "wrong_page"
    assert "推荐牛人" in diagnosis.action


def test_greeting_failure_formats_raw_message_with_action():
    text = format_greeting_failure_message("安全验证已完成，请重新发起本次手工打招呼")

    assert "BOSS 触发安全验证" in text
    assert "原始信息" in text
