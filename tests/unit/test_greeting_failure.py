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


def test_http_403_takes_priority_over_context_wording_and_stops_queue():
    diagnosis = diagnose_greeting_failure(
        "上下文打招呼失败: HTTP 403 请求未成功"
    )

    assert diagnosis.category == "risk_blocked"
    assert diagnosis.terminal is True
    assert "访问保护" in diagnosis.title


def test_ordinary_http_4xx_stops_the_current_send_chain_without_calling_it_risk():
    diagnosis = diagnose_greeting_failure("上下文打招呼失败: HTTP 404 接口不存在")

    assert diagnosis.category == "client_error"
    assert diagnosis.terminal is True
    assert "拒绝请求" in diagnosis.title
    assert "访问保护" not in diagnosis.title


def test_business_risk_code_takes_priority_over_context_wording():
    diagnosis = diagnose_greeting_failure(
        "上下文打招呼失败: 业务码 403 forbidden"
    )

    assert diagnosis.category == "risk_blocked"
    assert diagnosis.terminal is True


def test_unknown_greeting_failure_keeps_raw_detail_without_claiming_it_is_unrecognized():
    raw = "服务端返回 candidate status conflict"
    text = format_greeting_failure_message(raw)

    assert text.startswith("发送失败；")
    assert f"原始信息：{raw}" in text
    assert "未能识别" not in text
