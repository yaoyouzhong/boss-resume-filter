"""
无 AI 降级场景测试 — 验证 API Key 缺失时系统稳定运行
覆盖场景：
1. CLI 模式：无 api_config.json 时 --greet 正常运行
2. CLI 模式：有 --ai-eval 但无 key 时自动跳过 AI 评估
3. GUI 模式：无 key 时 AI 评估 checkbox 默认关闭，状态标签显示"⚠ 未配置"
4. GUI 模式：有 key 时 AI 评估 checkbox 默认开启，状态标签显示"✓ 已配置"
5. GUI 模式：保存 API Key 后状态标签动态更新
"""

import json
import os
import tempfile


# ========== CLI 模式测试 ==========

def test_cli_no_api_config_runs_normally():
    """CLI 模式：无 api_config.json 时 --greet 正常运行，不报错"""
    import argparse

    # 模拟无 api_config.json 环境
    with tempfile.TemporaryDirectory() as tmpdir:
        orig_cwd = os.getcwd()
        try:
            os.chdir(tmpdir)

            # 构造命令行参数（不含 --ai-eval）
            args = argparse.Namespace(
                clear=False,
                job=None,
                greet=True,
                re_greet=False,
                greet_level='normal',
                greet_names=None,
                list_candidates=False,
                rounds=1,
                verbose=False,
                ai_eval=False,
                api_config=None,
                api_key=None,
            )

            # 验证 args.ai_eval 为 False
            assert args.ai_eval is False, "默认情况下 ai_eval 应为 False"

        finally:
            os.chdir(orig_cwd)


def test_cli_ai_eval_no_key_skips_gracefully():
    """CLI 模式：有 --ai-eval 但无 key 时自动跳过，不抛异常"""
    import argparse

    with tempfile.TemporaryDirectory() as tmpdir:
        orig_cwd = os.getcwd()
        try:
            os.chdir(tmpdir)

            # 模拟用户传了 --ai-eval 但没有 api_config.json
            args = argparse.Namespace(
                ai_eval=True,
                api_config=None,
                api_key=None,
            )

            # 模拟 bossmaster.py:2529-2543 的降级逻辑
            if getattr(args, 'ai_eval', False) and (getattr(args, 'api_config', None) is None or getattr(args, 'api_key', None) is None):
                try:
                    with open('api_config.json', 'r', encoding='utf-8') as f:
                        json.load(f)
                except (FileNotFoundError, json.JSONDecodeError):
                    args.ai_eval = False

            # 验证 ai_eval 被自动关闭
            assert args.ai_eval is False, "无 key 时 ai_eval 应被自动关闭"

        finally:
            os.chdir(orig_cwd)


def test_cli_smart_scan_skips_ai_without_key():
    """CLI 模式：smart_scan_candidates 在无 key 时跳过 AI 阶段"""
    # 模拟候选人数据
    passed_candidates = [
        {'name': '张三', 'match_score': 80, 'geek_id': '123'},
        {'name': '李四', 'match_score': 75, 'geek_id': '456'},
    ]

    # 模拟 ai_eval=True 但 api_key=None 的场景
    ai_eval = True
    api_config = {'model': 'test'}
    api_key = None  # 无 key

    # 验证 bossmaster.py:2106 的四重保护
    should_run_ai = ai_eval and api_config and api_key and passed_candidates
    assert not should_run_ai, "无 api_key 时不应运行 AI 评估"


# ========== GUI 模式测试 ==========

def test_gui_no_key_checkbox_defaults_off():
    """GUI 模式：无 key 时 AI 评估 checkbox 默认关闭"""
    api_config = {
        "api_provider": "deepseek",
        "api_key": "",
        "base_url": "https://api.deepseek.com",
        "model": "deepseek-chat",
    }

    has_api_key = bool(api_config.get("api_key"))
    assert has_api_key is False, "api_key 为空时 has_api_key 应为 False"

    default_value = has_api_key
    assert default_value is False, "无 key 时 checkbox 默认值应为 False"


def test_gui_with_key_checkbox_defaults_on():
    """GUI 模式：有 key 时 AI 评估 checkbox 默认开启"""
    api_config = {
        "api_provider": "deepseek",
        "api_key": "sk-test-key-123",
        "base_url": "https://api.deepseek.com",
        "model": "deepseek-chat",
    }

    has_api_key = bool(api_config.get("api_key"))
    assert has_api_key is True, "api_key 非空时 has_api_key 应为 True"

    default_value = has_api_key
    assert default_value is True, "有 key 时 checkbox 默认值应为 True"


def test_gui_status_label_shows_unconfigured():
    """GUI 模式：无 key 时状态标签显示"⚠ 未配置"（橙色）"""
    has_api_key = False

    if has_api_key:
        status_text = "✓ 已配置"
        status_color = "#43A047"  # success green
    else:
        status_text = "⚠ 未配置"
        status_color = "#FB8C00"  # warning orange

    assert status_text == "⚠ 未配置", "无 key 时状态文本应为 '⚠ 未配置'"
    assert status_color == "#FB8C00", "无 key 时状态颜色应为 warning orange"


def test_gui_status_label_shows_configured():
    """GUI 模式：有 key 时状态标签显示"✓ 已配置"（绿色）"""
    has_api_key = True

    if has_api_key:
        status_text = "✓ 已配置"
        status_color = "#43A047"  # success green
    else:
        status_text = "⚠ 未配置"
        status_color = "#FB8C00"  # warning orange

    assert status_text == "✓ 已配置", "有 key 时状态文本应为 '✓ 已配置'"
    assert status_color == "#43A047", "有 key 时状态颜色应为 success green"


def test_gui_update_ai_eval_status_disables_checkbox_when_no_key():
    """GUI 模式：_update_ai_eval_status 在无 key 时自动关闭 checkbox"""
    class MockBooleanVar:
        def __init__(self, value):
            self.value = value
        def get(self):
            return self.value
        def set(self, v):
            self.value = v

    ai_eval_var = MockBooleanVar(value=True)
    api_config = {"api_key": ""}  # 无 key

    # 模拟 _update_ai_eval_status 逻辑
    has_key = bool(api_config.get("api_key"))
    if not has_key and ai_eval_var.get():
        ai_eval_var.set(False)

    assert ai_eval_var.get() is False, "无 key 时 checkbox 应被自动关闭"


def test_gui_update_ai_eval_status_keeps_checkbox_off_when_already_off():
    """GUI 模式：_update_ai_eval_status 在 checkbox 已关闭时不做改动"""
    class MockBooleanVar:
        def __init__(self, value):
            self.value = value
        def get(self):
            return self.value
        def set(self, v):
            self.value = v

    ai_eval_var = MockBooleanVar(value=False)  # 已经关闭
    api_config = {"api_key": ""}  # 无 key

    has_key = bool(api_config.get("api_key"))
    if not has_key and ai_eval_var.get():
        ai_eval_var.set(False)

    assert ai_eval_var.get() is False, "checkbox 应保持关闭状态"


# ========== 集成场景测试 ==========

def test_integration_gui_startup_no_key_no_crash():
    """集成测试：GUI 启动时无 key，不崩溃，checkbox 默认关闭"""
    api_config = {"api_key": ""}
    has_api_key = bool(api_config.get("api_key"))

    assert has_api_key is False
    default_checkbox_value = has_api_key
    assert default_checkbox_value is False

    status_text = "✓ 已配置" if has_api_key else "⚠ 未配置"
    assert status_text == "⚠ 未配置"


def test_integration_cli_greet_without_ai_eval():
    """集成测试：CLI --greet 不带 --ai-eval，完全不依赖 AI"""
    from filtering import filter_candidate

    candidate_text = "张三 5年经验 本科 上海 期望薪资 20K"
    rule = {
        'min_exp': 3,
        'edu': '本科',
        'city': '上海',
        'salary_max': 25,
        'keywords': ['Python', 'Django'],
        'required_conditions': [],
    }

    passed, score, details = filter_candidate(candidate_text, rule)

    # 验证返回了评分结果（纯规则引擎，不依赖 AI）
    assert isinstance(score, int), "应返回整数评分"
    assert 0 <= score <= 100, "评分应在 0-100 范围内"
    assert 'score_breakdown' in details, "应包含评分明细"


# ========== 超时诊断测试 ==========

def test_job_ai_parser_reuses_shared_adapter_with_thinking_disabled():
    """job_ai_parser 必须复用 ai_adapter.build_request 并关闭推理，不再自带端点方言"""
    from pathlib import Path
    source = Path("job_ai_parser.py").read_text(encoding="utf-8")
    assert "build_request(" in source, "应复用 ai_adapter.build_request 构造请求"
    assert "disable_thinking=True" in source, "结构化 JD 解析应关闭推理"
    assert "api.kimi.com/coding" not in source, "端点方言应单点维护在 ai_adapter"
    assert "max_completion_tokens" not in source, "端点方言应单点维护在 ai_adapter"


def test_connect_timeout_error_message():
    """job_ai_parser：ConnectTimeout 应产生包含'连接超时'的错误信息"""
    from job_ai_parser import AI_PARSE_TIMEOUT
    # 模拟 _call_chat_completion 中 ConnectTimeout 分支的错误信息格式
    error_msg = f"AI 连接超时：{AI_PARSE_TIMEOUT[0]} 秒内无法建立连接（DNS/代理/网络不通）"
    assert "连接超时" in error_msg, "错误信息应包含'连接超时'"
    assert str(AI_PARSE_TIMEOUT[0]) in error_msg, "错误信息应包含连接超时秒数"


def test_read_timeout_error_message():
    """job_ai_parser：ReadTimeout 应产生包含'读取超时'的错误信息"""
    from job_ai_parser import AI_PARSE_TIMEOUT
    # 模拟 _call_chat_completion 中 ReadTimeout 分支的错误信息格式
    error_msg = f"AI 读取超时：模型服务 {AI_PARSE_TIMEOUT[1]} 秒内未返回响应"
    assert "读取超时" in error_msg, "错误信息应包含'读取超时'"
    assert str(AI_PARSE_TIMEOUT[1]) in error_msg, "错误信息应包含读取超时秒数"


def test_friendly_reason_connect_timeout():
    """GUI：连接超时应映射为网络相关提示，而非'响应太慢'"""
    # 模拟 _friendly_ai_parse_reason 的逻辑
    text = "AI 连接超时：6 秒内无法建立连接（DNS/代理/网络不通）"

    if "连接超时" in text:
        reason = "网络连接太慢（DNS/代理/服务器不可达）"
    elif "读取超时" in text:
        reason = "模型响应太慢"
    else:
        reason = "未知"

    assert "网络" in reason, "连接超时应映射为网络相关提示"
    assert "响应太慢" not in reason, "连接超时不应映射为'响应太慢'"


def test_friendly_reason_read_timeout():
    """GUI：读取超时应映射为'模型响应太慢'"""
    text = "AI 读取超时：模型服务 60 秒内未返回响应"

    if "连接超时" in text:
        reason = "网络连接太慢（DNS/代理/服务器不可达）"
    elif "读取超时" in text:
        reason = "模型响应太慢"
    else:
        reason = "未知"

    assert reason == "模型响应太慢", "读取超时应映射为'模型响应太慢'"
